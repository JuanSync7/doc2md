"""
title: Failure classification + elastic scheduling policy (private)
layer: backend
public_api: no
summary: Pure policy for the self-healing converter — classify a worker failure, choose the recovery action (retry / escalate to a bigger solo lane / fall back / blacklist), plan elastic admission (grow/hold/shrink) from live machine load, and order work biggest-first.
"""
# 3.6-compatible. Stdlib only. Pure policy — no disk, no network, no processes.
# The IO/orchestration that USES these decisions lives in scripts/ (the Python 3.12
# supervisor + worker) and .slurm/; this module only DECIDES, so every rule here is
# unit-testable without spawning anything.
import re
from collections import namedtuple

__all__ = [
    "classify_failure", "recovery_action", "RecoveryAction",
    "plan_admission", "AdmissionPlan", "order_todo",
    "FAIL_NONE", "FAIL_OOM", "FAIL_HANG", "FAIL_DOCLING", "FAIL_TRANSIENT",
    "FAIL_INPUT", "FAIL_UNKNOWN", "FAIL_CLASSES", "DOCLING_BAD_STATUS",
    "ACT_RETRY", "ACT_ESCALATE", "ACT_FALLBACK", "ACT_BLACKLIST",
    "ADM_GROW", "ADM_HOLD", "ADM_SHRINK",
]

# --- failure classes (what went wrong) --------------------------------------
FAIL_NONE      = "none"        # not a failure (clean exit with output)
FAIL_OOM       = "oom"         # killed for memory (kernel OOM / bad_alloc / MemoryError)
FAIL_HANG      = "hang"        # watchdog killed it: log-silent AND cpu-idle
FAIL_DOCLING   = "docling"     # docling ran but returned FAILURE/PARTIAL (deterministic bad output)
FAIL_TRANSIENT = "transient"   # non-zero exit, no OOM/hang/input signature — worth one more try
FAIL_INPUT     = "input"       # source unreadable / empty / encrypted / corrupt — retry can't help
FAIL_UNKNOWN   = "unknown"
FAIL_CLASSES = (FAIL_NONE, FAIL_OOM, FAIL_HANG, FAIL_DOCLING,
                FAIL_TRANSIENT, FAIL_INPUT, FAIL_UNKNOWN)

# --- recovery actions (what to do about it) ---------------------------------
ACT_RETRY     = "retry"        # run again, same resources (transient hiccup)
ACT_ESCALATE  = "escalate"     # re-run SOLO in a bigger lane (more threads + reserved RAM)
ACT_FALLBACK  = "fallback"     # stop trying docling; use the text-layer / native cross-check body
ACT_BLACKLIST = "blacklist"    # give up on this doc (native extraction at build_index)

# --- admission actions (elastic concurrency) --------------------------------
ADM_GROW   = "grow"            # spare CPU+RAM -> add a worker
ADM_HOLD   = "hold"            # at target -> no change
ADM_SHRINK = "shrink"          # oversubscribed -> yield a worker (good citizen)

DOCLING_BAD_STATUS = ("FAILURE", "PARTIAL_SUCCESS")

_OOM_PAT = re.compile(r"(?i)(out of memory|cannot allocate memory|std::bad_alloc|"
                      r"memoryerror|oom-kill|killed process|\bkilled\b)")
_INPUT_PAT = re.compile(r"(?i)(no such file|cannot open|not a pdf|emptyfile|"
                        r"zero[- ]?size|corrupt|damaged|unsupported (file|format)|"
                        r"password[- ]?protected|\bencrypted\b)")


def _signal_of(returncode):
    # type: (int) -> int
    """Signal a process died from, from a subprocess returncode (``-N``) or a shell
    code (``128+N``). ``0`` if it wasn't signalled."""
    if returncode is None:
        return 0
    rc = int(returncode)
    if rc < 0:
        return -rc
    if rc > 128:
        return rc - 128
    return 0


def classify_failure(returncode=0, watchdog_killed=False, log_tail="",
                     had_md=False, status=None):
    # type: (int, bool, str, bool, str) -> str
    """Name what went wrong so recovery can respond in kind. Most-specific first.

      watchdog_killed          -> HANG      (log-silent + cpu-idle; retrying as-is re-hangs)
      SIGKILL(9) w/o watchdog,
        or memory text in log   -> OOM       (needs MORE ram, not the same lane again)
      input signature in log    -> INPUT     (bad/encrypted/corrupt source — retry is futile)
      docling bad status        -> DOCLING   (deterministic; re-running yields the same output)
      clean exit, output present -> NONE
      clean exit, NO output      -> DOCLING  (succeeded but produced nothing usable)
      any other non-zero exit    -> TRANSIENT
    All inputs are plain values (no processes touched) -> unit-testable."""
    tail = log_tail or ""
    sig = _signal_of(returncode)
    if watchdog_killed:
        return FAIL_HANG
    # A kernel OOM-kill lands as SIGKILL(9). A watchdog kill is also SIGKILL but is
    # caught above, so a bare SIGKILL here is memory pressure. Also match the log text.
    if sig == 9 or _OOM_PAT.search(tail):
        return FAIL_OOM
    if _INPUT_PAT.search(tail):
        return FAIL_INPUT
    if status and str(status).upper() in DOCLING_BAD_STATUS:
        return FAIL_DOCLING
    rc = 0 if returncode is None else int(returncode)
    if rc == 0:
        return FAIL_NONE if had_md else FAIL_DOCLING
    return FAIL_TRANSIENT


RecoveryAction = namedtuple("RecoveryAction", ["action", "threads", "mem_gb", "reason"])


def recovery_action(failure_class, attempts_used, cfg, cpu=0):
    # type: (str, int, object, int) -> RecoveryAction
    """Decide what to do about a failed doc, given how many recovery attempts it has
    already had. All resource numbers come from ``cfg`` (nothing hardcoded).

      OOM / HANG  -> ESCALATE to a solo bigger lane (more threads + reserved RAM) until
                     ``cfg.escalation_attempts`` is spent, then FALLBACK. (Re-running in
                     the SAME lane just re-OOMs/re-hangs; a bigger solo lane is what
                     rescued the 1993-page standard.)
      DOCLING     -> FALLBACK immediately: docling is deterministic, so re-running gives
                     the identical output — the remedy is the text-layer / native body.
      TRANSIENT   -> RETRY (same size) until ``cfg.retry_attempts``, then FALLBACK.
      INPUT       -> BLACKLIST (a corrupt/encrypted source will never convert).
      NONE / UNKNOWN -> RETRY once (defensive).

    Returns ``(action, threads, mem_gb, reason)``; the supervisor uses threads/mem_gb to
    size the escalation lane."""
    fc = failure_class
    tps = int(cfg.threads_per_shard)
    mps = float(cfg.mem_per_shard_gb)
    big_threads = int(cfg.big_doc_threads) or (int(cpu) if cpu else tps)
    big_mem = float(cfg.big_doc_mem_gb)
    if fc in (FAIL_OOM, FAIL_HANG):
        if attempts_used < int(cfg.escalation_attempts):
            return RecoveryAction(ACT_ESCALATE, big_threads, big_mem,
                                  "%s -> solo lane (%d threads, %.0fGB reserved)"
                                  % (fc, big_threads, big_mem))
        return RecoveryAction(ACT_FALLBACK, tps, mps,
                              "%s persists after %d escalation(s) -> fallback body"
                              % (fc, attempts_used))
    if fc == FAIL_DOCLING:
        return RecoveryAction(ACT_FALLBACK, tps, mps,
                              "docling deterministic bad output -> fallback body")
    if fc == FAIL_INPUT:
        return RecoveryAction(ACT_BLACKLIST, 0, 0.0,
                              "unreadable/encrypted/corrupt source -> blacklist (native at build_index)")
    if fc == FAIL_TRANSIENT:
        if attempts_used < int(cfg.retry_attempts):
            return RecoveryAction(ACT_RETRY, tps, mps, "transient -> retry (same lane)")
        return RecoveryAction(ACT_FALLBACK, tps, mps, "transient persists -> fallback body")
    # NONE / UNKNOWN
    return RecoveryAction(ACT_RETRY, tps, mps, "%s -> retry (defensive)" % fc)


AdmissionPlan = namedtuple("AdmissionPlan", ["action", "reason"])


def plan_admission(loadavg1, mem_avail_gb, running_workers, cpu, cfg):
    # type: (float, float, int, int, object) -> AdmissionPlan
    """Good-citizen elastic control from LIVE machine state.

    Aims total system load at ~``cpu`` cores: yield a worker when the box is
    oversubscribed (someone else spiked the load), add one when cores AND RAM are
    genuinely free. Thresholds are fractions of ``cpu`` from ``cfg`` — nothing hardcoded.
    ``running_workers`` counts the converter processes; each uses ``threads_per_shard``
    cores and ``mem_per_shard_gb`` RAM headroom."""
    cpu = int(cpu) or 1
    tps = max(1, int(cfg.threads_per_shard))
    hi = cpu * float(cfg.load_high_frac)
    lo = cpu * float(cfg.load_low_frac)
    if loadavg1 > hi and running_workers > 1:
        return AdmissionPlan(ADM_SHRINK,
                             "load %.1f > %.1f (cpu*%.2f) -> yield a worker"
                             % (loadavg1, hi, float(cfg.load_high_frac)))
    projected = (running_workers + 1) * tps
    if loadavg1 < lo and projected <= cpu and mem_avail_gb >= float(cfg.mem_per_shard_gb):
        return AdmissionPlan(ADM_GROW,
                             "load %.1f < %.1f, %d cores + %.0fGB free -> add a worker"
                             % (loadavg1, lo, cpu - running_workers * tps, mem_avail_gb))
    return AdmissionPlan(ADM_HOLD, "load %.1f within band [%.1f, %.1f]" % (loadavg1, lo, hi))


def order_todo(items):
    # type: (list) -> list
    """Order work biggest-first so a huge doc (e.g. a 2000-page PDF) starts EARLY and is
    not the lone straggler at the tail while every other worker sits idle. ``items`` is a
    list of ``(key, size)``; returns the keys, largest ``size`` first, ties broken by key
    for determinism (``size`` may be ``None`` -> treated as 0)."""
    return [k for k, _ in sorted(items, key=lambda kv: (-(kv[1] or 0), kv[0]))]
