#!/usr/bin/env python3
"""
title: Elastic self-healing conversion supervisor (entrypoint)
layer: scripts
summary: Orchestrate work-stealing converter workers with live admission control (grow/hold/shrink from machine load), a watchdog, failure-classified recovery (retry / solo big-lane escalation / fallback body / blacklist), and a _heal_status.json rollup.

Runs N `docling_convert.py --queue` workers over the shared claim queue and keeps
the corpus converging to a TERMINAL state for every doc: `valid` (validator-passed
markdown), `valid via fallback body`, or `blacklisted with a reason`. Nothing is
silently skipped and nothing is retried forever.

All POLICY comes from `backend.ingest` (pure, unit-tested): `plan_admission`
decides grow/hold/shrink from live load+RAM, `classify_failure` names what killed
a worker (OOM / hang / docling / transient / input), `recovery_action` picks the
ladder rung (retry -> solo big-mem escalation lane -> independent fallback body ->
blacklist), `recommend_shards` sizes the initial fleet. This script only does the
process IO around those decisions.

Safe to run twice: claims + validator records make workers idempotent, stale
claims from dead runs are released at startup, and a supervisor that finds no
remaining work exits immediately.

Usage:
  .venv/bin/python scripts/heal_supervisor.py                  # heal whole corpus
  .venv/bin/python scripts/heal_supervisor.py --status-only    # print rollup, exit
Env: DOC2MD_HEAL_TICK / DOC2MD_STALL_SECS / DOC2MD_BUSY_TICKS override the loop knobs.
"""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))
sys.path.insert(0, _HERE)
from backend.ingest import (load_ingest_config, load_source_root, plan_admission,
                            classify_failure, recovery_action, recommend_shards,
                            FAIL_NONE, FAIL_DOCLING,
                            ACT_RETRY, ACT_ESCALATE, ACT_FALLBACK, ACT_BLACKLIST,
                            ADM_GROW, ADM_SHRINK)
import docling_convert as dc

REPO = os.path.dirname(_HERE)


# --- small pure helpers (unit-testable without processes) --------------------

def _watchdog_verdict(silent_secs, busy_ticks_sample, stall_secs, busy_min):
    """'ok' while the log is fresh; on stall, 'spare' a worker still burning CPU
    (big docs convert silently) and 'kill' only one that is silent AND idle."""
    if silent_secs < stall_secs:
        return "ok"
    return "spare" if busy_ticks_sample >= busy_min else "kill"


def _log_tail(path, nbytes=4096, start=0):
    """Last ``nbytes`` of a log, never reaching before ``start`` — logs are opened
    append-mode under stable keys, so ``start`` scopes the tail to the CURRENT
    attempt (a previous attempt's failure text must not steer classification)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(int(start), size - nbytes))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


_DOC_ECHO_PREFIXES = ("[hb ", "[validate]", "[figures]", "[coverage]", "[fallback",
                      "[only]", "CONVERT ")


def _classify_tail(tail):
    """Strip document-derived lines (heartbeats echo doc FILENAMES, validator lines
    echo missing source VOCABULARY) before failure classification, so a doc named
    'password-protected-notes.pdf' or missing-token lists can't misroute the
    recovery ladder."""
    kept = []
    for ln in (tail or "").splitlines():
        if ln.lstrip().startswith(_DOC_ECHO_PREFIXES):
            continue
        kept.append(ln)
    return "\n".join(kept)


def _mem_avail_gb():
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024.0 * 1024.0)
    except (OSError, ValueError):
        pass
    return 0.0


def _cpu_ticks(pid):
    """CPU ticks of a worker's WHOLE process tree, not just its own pid.

    Workers are session leaders (``start_new_session=True`` -> sid == worker pid),
    so summing utime+stime over every /proc process whose session id matches
    catches work happening in children (pdftotext, soffice, docling subprocesses).
    The worker's own cutime/cstime add already-reaped children. A pid-only sample
    would kill a busy-in-child worker as 'hung'. 0 if unreadable."""
    def fields(p):
        with open("/proc/%s/stat" % p) as fh:
            return fh.read().rsplit(")", 1)[1].split()
    total = 0
    try:
        rest = fields(pid)
        total += int(rest[11]) + int(rest[12])          # utime+stime
        total += int(rest[13]) + int(rest[14])          # cutime+cstime (reaped kids)
    except (OSError, IndexError, ValueError):
        return 0
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == pid:
                continue
            try:
                rest = fields(entry)
                if int(rest[3]) == pid:                  # same session as the worker
                    total += int(rest[11]) + int(rest[12])
            except (OSError, IndexError, ValueError):
                continue
    except OSError:
        pass
    return total


def _kill_tree(proc):
    """SIGKILL the worker's whole process group (it is a pgroup leader via
    ``start_new_session=True``), so wedged children (soffice, pdftotext) die with
    it instead of being orphaned."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _release_stale_claims(out_dir, done_ids):
    """Remove claims whose owner is dead while the doc is still not valid —
    leftovers of a previous run that died mid-doc. Claims record
    ``owner pid epoch host``: a pid can only be probed on ITS OWN host, so claims
    written from another NFS client are left alone (an orphaned foreign worker may
    still be converting; stealing its claim would double-convert the doc).
    Returns released doc ids."""
    d = os.path.join(out_dir, "_claims")
    if not os.path.isdir(d):
        return []
    released = []
    for fp in sorted(os.listdir(d)):
        if not fp.endswith(".claim"):
            continue
        did = fp[:-len(".claim")]
        path = os.path.join(d, fp)
        if did in done_ids:
            continue                      # finished work keeps its claim (idempotence)
        if not dc._claim_owner_alive(path):
            try:
                os.remove(path)
                released.append(did)
            except OSError:
                pass
    return released


def _claimed_ids(out_dir):
    d = os.path.join(out_dir, "_claims")
    if not os.path.isdir(d):
        return set()
    return set(f[:-len(".claim")] for f in os.listdir(d) if f.endswith(".claim"))


def _blacklisted_ids(out_dir):
    ids = set()
    import glob as _g
    for cf in _g.glob(os.path.join(out_dir, "_crashed*.txt")):
        try:
            ids |= set(l.strip() for l in open(cf) if l.strip())
        except OSError:
            pass
    return ids


# --- the supervisor -----------------------------------------------------------

class Supervisor(object):
    def __init__(self, args, cfg):
        self.args = args
        self.cfg = cfg
        self.cpu = os.cpu_count() or 1
        self.tps = args.threads or cfg.threads_per_shard
        self.big_threads = cfg.big_doc_threads or self.cpu
        self.out = args.out
        self.log_dir = os.path.join(args.out, "_logs")
        os.makedirs(self.log_dir, exist_ok=True)
        self.worker_cmd = shlex.split(args.worker_cmd) if args.worker_cmd else [
            sys.executable, os.path.join(_HERE, "docling_convert.py")]
        self.workers = {}       # wid -> state dict
        self.lanes = {}         # lane key -> state dict (escalation/fallback lanes)
        self.esc_runs = {}      # did -> escalation attempts used
        self.retry_runs = {}    # did -> same-lane retries used
        self.fb_runs = {}       # did -> fallback attempts used
        self.pending_esc = []   # (did, mem_gb) waiting for RAM headroom (solo big lane)
        self.events = []
        self.next_wid = 0
        self.next_lane_wid = 900   # monotonic: lane ids must NEVER be reused while a
        #                            lane lives (a reused id would misread another
        #                            lane's _converting marker as its own crash)
        self.respawns_left = args.max_respawns
        self.stopping = False
        self.drain_deadline = 0.0
        self.drain_started = False
        # admission must model workers at their ACTUAL thread width (args.threads may
        # override config); a mismatched cfg would admit oversubscription
        self.adm_cfg = cfg._replace(threads_per_shard=self.tps)
        _sources, _ = dc.find_sources(args.src)
        self.rows = dc.plan(_sources, args.out, False)
        self.by_id = dict((r["id"], r) for r in self.rows)

    # -- state queries ---------------------------------------------------------
    def done_ids(self):
        return dc._done_ids(self.out)

    def remaining(self, done=None):
        done = self.done_ids() if done is None else done
        black = _blacklisted_ids(self.out)
        return [r for r in self.rows if r["id"] not in done and r["id"] not in black]

    def _event(self, msg):
        line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
        self.events.append(line)
        self.events = self.events[-60:]
        print("  [supervisor] %s" % msg, file=sys.stderr)
        sys.stderr.flush()

    # -- process management ------------------------------------------------------
    def _spawn(self, argv_extra, key, kind, doc=None, threads=None):
        log = os.path.join(self.log_dir, "%s.log" % key)
        cmd = list(self.worker_cmd) + argv_extra + [
            "--src", self.args.src, "--out", self.out, "--ocr", self.args.ocr]
        lf = open(log, "ab")
        log_off = lf.tell()      # scope failure-classification tails to THIS attempt
        proc = subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT,
                                cwd=REPO, start_new_session=True)
        lf.close()
        state = {"proc": proc, "log": log, "log_off": log_off, "kind": kind, "doc": doc,
                 "key": key, "started": time.time(), "spared_at": 0.0,
                 "watchdog_killed": False, "draining": False, "threads": threads or self.tps}
        self._event("spawn %s pid=%d%s" % (key, proc.pid, (" doc=" + doc) if doc else ""))
        return state

    def _lane_alive_for(self, did):
        return any(st["doc"] == did and st["proc"].poll() is None
                   for st in self.lanes.values())

    def spawn_worker(self):
        wid = self.next_wid
        self.next_wid += 1
        st = self._spawn(["--queue", "--worker-id", str(wid), "--threads", str(self.tps)],
                         "worker_%d" % wid, "worker")
        st["wid"] = wid
        self.workers[wid] = st

    def spawn_escalation(self, did):
        if self._lane_alive_for(did):
            self._event("escalation for %s skipped: a lane is already running it" % did)
            return
        self.esc_runs[did] = self.esc_runs.get(did, 0) + 1
        wid = self.next_lane_wid
        self.next_lane_wid += 1
        st = self._spawn(["--only", did, "--reclaim",
                          "--worker-id", str(wid),
                          "--threads", str(self.big_threads)],
                         "escalate_%s" % did, "escalate", doc=did, threads=self.big_threads)
        st["wid"] = wid
        self.lanes["escalate_%s" % did] = st

    def spawn_fallback(self, did):
        if self._lane_alive_for(did):
            self._event("fallback for %s skipped: a lane is already running it" % did)
            return
        self.fb_runs[did] = self.fb_runs.get(did, 0) + 1
        wid = self.next_lane_wid
        self.next_lane_wid += 1
        st = self._spawn(["--only", did, "--reclaim", "--fallback-only",
                          "--worker-id", str(wid)],
                         "fallback_%s" % did, "fallback", doc=did, threads=1)
        st["wid"] = wid
        self.lanes["fallback_%s" % did] = st

    def blacklist(self, did, reason):
        path = os.path.join(self.out, "_crashed.supervisor.txt")
        with open(path, "a") as f:
            f.write(did + "\n")
        rel = self.by_id.get(did, {}).get("rel", did)
        self._event("BLACKLIST %s (%s)" % (rel, reason))

    # -- recovery ladder --------------------------------------------------------
    def _release_claim(self, did):
        try:
            os.remove(dc._claim_path(self.out, did))
        except OSError:
            pass

    def _recover(self, did, failure_class):
        attempts = (self.esc_runs.get(did, 0) if failure_class in ("oom", "hang")
                    else self.retry_runs.get(did, 0))
        act = recovery_action(failure_class, attempts, self.cfg, self.cpu)
        rel = self.by_id.get(did, {}).get("rel", did)
        self._event("recover %s: class=%s attempts=%d -> %s (%s)"
                    % (rel, failure_class, attempts, act.action, act.reason))
        # The dead worker's claim is released ONLY for RETRY (a queue worker should
        # pick the doc up). Escalation/fallback lanes take it over via --reclaim
        # (safe: reclaim refuses live owners) — releasing it here would let a live
        # queue worker grab the doc in the lane's startup window (double-convert).
        if act.action == ACT_ESCALATE:
            # solo big lane needs RAM headroom; queue it and let the tick spawn it
            # when memory is actually free (spawning into the pressure that OOMed
            # the doc would deterministically re-OOM)
            self.pending_esc.append((did, act.mem_gb))
            self._event("escalation for %s queued (needs %.0fGB free)" % (rel, act.mem_gb))
        elif act.action == ACT_FALLBACK:
            if self.fb_runs.get(did, 0) == 0:
                self.spawn_fallback(did)
            else:
                self.blacklist(did, "fallback already tried and failed")
        elif act.action == ACT_BLACKLIST:
            self._release_claim(did)
            self.blacklist(did, act.reason)
        elif act.action == ACT_RETRY:
            self.retry_runs[did] = self.retry_runs.get(did, 0) + 1
            self._release_claim(did)
            # released claim -> any queue worker (or a respawned one) picks it up

    def _handle_worker_exit(self, wid):
        st = self.workers.pop(wid)
        rc = st["proc"].returncode
        tail = _classify_tail(_log_tail(st["log"], start=st.get("log_off", 0)))
        victim_file = os.path.join(self.out, "_converting.w%d.txt" % wid)
        victim = None
        if os.path.isfile(victim_file):
            try:
                victim = open(victim_file).read().strip() or None
                os.remove(victim_file)
            except OSError:
                pass
        fc = classify_failure(returncode=rc, watchdog_killed=st["watchdog_killed"],
                              log_tail=tail, had_md=True)
        if st["draining"]:
            try:
                os.remove(os.path.join(self.out, "_stop.w%d.txt" % wid))
            except OSError:
                pass
        self._event("worker_%d exit rc=%s class=%s victim=%s" % (wid, rc, fc, victim))
        if self.stopping:
            # shutdown drain: an interrupted doc is NOT a failure — release its claim
            # so the next run reclaims it; never spawn new lanes or burn attempts here
            if victim:
                self._release_claim(victim)
            return
        if victim and victim in self.by_id:
            # died mid-doc -> per-doc recovery (a clean exit removes the marker)
            self._recover(victim, fc if fc != FAIL_NONE else "transient")
        elif rc not in (0, None) and not st["watchdog_killed"]:
            # worker-level crash without a victim: transient environment trouble
            self._event("worker_%d died without a victim (rc=%s)" % (wid, rc))

    def _handle_lane_exit(self, key):
        st = self.lanes.pop(key)
        did = st["doc"]
        rc = st["proc"].returncode
        # lanes have worker-ids too: clean their victim marker so a later lane with
        # a fresh id never misreads it, and stale files don't accumulate
        try:
            os.remove(os.path.join(self.out, "_converting.w%d.txt" % st.get("wid", -1)))
        except OSError:
            pass
        done = self.done_ids()
        rel = self.by_id.get(did, {}).get("rel", did)
        if did in done:
            self._event("%s SUCCEEDED for %s" % (st["kind"], rel))
            return
        if self.stopping:
            self._release_claim(did)          # interrupted, not failed
            return
        tail = _classify_tail(_log_tail(st["log"], start=st.get("log_off", 0)))
        fc = classify_failure(returncode=rc, watchdog_killed=st["watchdog_killed"],
                              log_tail=tail, had_md=False)
        if st["kind"] == "fallback":
            self._release_claim(did)
            self.blacklist(did, "fallback body failed (class=%s rc=%s)" % (fc, rc))
            return
        # escalation lane failed: docling-deterministic or converted-but-invalid ->
        # fallback; resource death -> next rung of the ladder (more escalation/fallback)
        self._recover(did, fc if fc not in (FAIL_NONE,) else FAIL_DOCLING)

    def _watchdog_all(self, states):
        """One BATCHED CPU sample for every stalled process: collect them all, sleep
        the 3s window once, then verdict each — a serial per-process sleep would
        block the tick loop for 3*N seconds and misread processes that exit inside
        another's window."""
        now = time.time()
        stalled = []
        for st in states:
            try:
                mtime = os.path.getmtime(st["log"])
            except OSError:
                mtime = st["started"]
            silent = now - max(mtime, st["spared_at"], st["started"])
            if silent >= self.args.stall_secs and st["proc"].poll() is None:
                stalled.append((st, silent, _cpu_ticks(st["proc"].pid)))
        if not stalled:
            return
        time.sleep(3)
        for st, silent, t0 in stalled:
            if st["proc"].poll() is not None:
                continue                      # exited during the window — reaped next tick
            busy = _cpu_ticks(st["proc"].pid) - t0
            verdict = _watchdog_verdict(silent, busy, self.args.stall_secs, self.args.busy_ticks)
            if verdict == "spare":
                st["spared_at"] = now
                self._event("%s silent %.0fs but busy (%d ticks/3s) — sparing"
                            % (st["key"], silent, busy))
            elif verdict == "kill":
                st["watchdog_killed"] = True
                self._event("%s HUNG (silent %.0fs, idle) — killing" % (st["key"], silent))
                _kill_tree(st["proc"])

    # -- admission (good-citizen elasticity) -------------------------------------
    def _admission(self, unclaimed_count):
        try:
            load1 = float(open("/proc/loadavg").read().split()[0])
        except (OSError, ValueError):
            load1 = 0.0
        mem_gb = _mem_avail_gb()
        # Reserve headroom for capacity that hasn't peaked yet: MemAvailable only
        # reflects pages already allocated, so workers still ramping (younger than
        # ramp_secs), live escalation lanes, and queued escalations must be deducted
        # or successive GROW ticks overcommit RAM.
        now = time.time()
        ramping = sum(1 for w in self.workers.values()
                      if now - w["started"] < self.args.ramp_secs)
        reserve = (self.cfg.mem_per_shard_gb * ramping
                   + sum(self.cfg.big_doc_mem_gb for st in self.lanes.values()
                         if st["kind"] == "escalate")
                   + sum(m for _, m in self.pending_esc))
        mem_eff = max(0.0, mem_gb - reserve)
        # a big escalation lane occupies big_threads/tps worker-slots of the machine
        effective = len(self.workers) + sum(
            max(1, st["threads"] // self.tps) for st in self.lanes.values())
        plan = plan_admission(load1, mem_eff, effective, self.cpu, self.adm_cfg)
        if plan.action == ADM_GROW:
            active = len(self.workers)
            if (unclaimed_count > active and self.respawns_left > 0
                    and not self.pending_esc
                    and (not self.args.max_workers or active < self.args.max_workers)):
                self.respawns_left -= 1
                self.spawn_worker()
                self._event("admission GROW: %s" % plan.reason)
        elif plan.action == ADM_SHRINK:
            # one actuation at a time: loadavg lags ~1 min behind reality, so draining
            # a worker every tick on a transient spike would collapse the whole fleet
            if not any(w["draining"] for w in self.workers.values()):
                candidates = [w for w in self.workers.values() if not w["draining"]]
                if candidates:
                    w = max(candidates, key=lambda s: s["wid"])
                    open(os.path.join(self.out, "_stop.w%d.txt" % w["wid"]), "w").close()
                    w["draining"] = True
                    self._event("admission SHRINK (%s): draining worker_%d"
                                % (plan.reason, w["wid"]))
        return load1, mem_gb, plan

    def _pump_escalations(self, mem_gb):
        """Spawn queued escalations only when their reserved RAM is actually free;
        while one waits, drain a worker to free memory rather than spawning the big
        lane into the same pressure that OOM-killed the doc."""
        if not self.pending_esc:
            return
        did, need = self.pending_esc[0]
        if self._lane_alive_for(did):
            self.pending_esc.pop(0)
            return
        if mem_gb >= need:
            self.pending_esc.pop(0)
            self.spawn_escalation(did)
            return
        if not any(w["draining"] for w in self.workers.values()) and self.workers:
            w = max(self.workers.values(), key=lambda s: s["wid"])
            open(os.path.join(self.out, "_stop.w%d.txt" % w["wid"]), "w").close()
            w["draining"] = True
            self._event("draining worker_%d to free %.0fGB for escalation of %s"
                        % (w["wid"], need, did))

    # -- rollup -------------------------------------------------------------------
    def _write_status(self, phase, load1, mem_gb, plan, remaining):
        st = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "phase": phase, "cpu": self.cpu, "load1": load1,
            "mem_avail_gb": round(mem_gb, 1),
            "plan": {"action": plan.action, "reason": plan.reason} if plan else None,
            "workers": [{"id": w["wid"], "pid": w["proc"].pid,
                         "age_s": int(time.time() - w["started"]),
                         "draining": w["draining"]} for w in self.workers.values()],
            "lanes": [{"key": k, "kind": v["kind"], "doc": v["doc"],
                       "age_s": int(time.time() - v["started"])}
                      for k, v in self.lanes.items()],
            "totals": {"docs": len(self.rows), "remaining": len(remaining),
                       "blacklisted": len(_blacklisted_ids(self.out)),
                       "claims": len(_claimed_ids(self.out))},
            "attempts": {"escalations": self.esc_runs, "retries": self.retry_runs,
                         "fallbacks": self.fb_runs},
            "pending_escalations": [d for d, _ in self.pending_esc],
            "events": self.events[-40:],
        }
        tmp = self.args.status_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, self.args.status_file)

    # -- main loop -------------------------------------------------------------------
    def run(self):
        done = self.done_ids()
        released = _release_stale_claims(self.out, done)
        if released:
            self._event("released %d stale claim(s) from a previous run" % len(released))
        for f in os.listdir(self.out):
            if f.startswith("_stop.w"):
                try:
                    os.remove(os.path.join(self.out, f))
                except OSError:
                    pass
        remaining = self.remaining(done)
        if not remaining:
            self._event("nothing to heal: all %d docs are valid or blacklisted" % len(self.rows))
            self._write_status("done", 0.0, 0.0, None, remaining)
            return 0
        mem_gb = _mem_avail_gb()
        n0, _ = recommend_shards(self.cpu, mem_gb, self.tps,
                                 self.cfg.mem_per_shard_gb, self.cfg.max_shards)
        n0 = min(n0, len(remaining),
                 self.args.max_workers or n0)
        self._event("healing %d doc(s): starting %d worker(s) x %d threads"
                    % (len(remaining), n0, self.tps))
        for _ in range(n0):
            self.spawn_worker()

        signal.signal(signal.SIGTERM, self._sig)
        signal.signal(signal.SIGINT, self._sig)
        load1, plan = 0.0, None
        while True:
            time.sleep(self.args.tick)
            for wid in [w for w, s in self.workers.items() if s["proc"].poll() is not None]:
                self._handle_worker_exit(wid)
            for key in [k for k, s in self.lanes.items() if s["proc"].poll() is not None]:
                self._handle_lane_exit(key)
            if self.stopping:
                # drain phase (the signal handler only sets flags — all IO is here):
                # ask workers to yield, hard-kill whatever outlives the deadline
                if not self.drain_started:
                    self.drain_started = True
                    self.pending_esc = []
                    for w in self.workers.values():
                        open(os.path.join(self.out, "_stop.w%d.txt" % w["wid"]), "w").close()
                        w["draining"] = True
                    self._event("draining %d worker(s)/%d lane(s), deadline %ds"
                                % (len(self.workers), len(self.lanes), self.args.drain_secs))
                if time.time() > self.drain_deadline:
                    for s in list(self.workers.values()) + list(self.lanes.values()):
                        if s["proc"].poll() is None:
                            _kill_tree(s["proc"])
                if not self.workers and not self.lanes:
                    break
                continue
            done = self.done_ids()
            remaining = self.remaining(done)
            claimed = _claimed_ids(self.out)
            unclaimed = [r for r in remaining if r["id"] not in claimed]
            self._watchdog_all(list(self.workers.values()) + list(self.lanes.values()))
            load1, mem_gb, plan = self._admission(len(unclaimed))
            self._pump_escalations(mem_gb)
            # keep at least one lane alive while claimable work remains
            if (unclaimed and not self.workers and not self.lanes
                    and not self.pending_esc and self.respawns_left > 0):
                self.respawns_left -= 1
                self.spawn_worker()
            self._write_status("running", load1, mem_gb, plan, remaining)
            if not self.workers and not self.lanes and not self.pending_esc:
                # drained: anything left gets ONE fallback pass, then judgment
                todo_fb = [r for r in remaining if self.fb_runs.get(r["id"], 0) == 0]
                if todo_fb:
                    self._event("final sweep: fallback for %d unresolved doc(s)" % len(todo_fb))
                    for r in todo_fb[:8]:
                        self.spawn_fallback(r["id"])
                    continue
                if not remaining:
                    break
                # fallbacks all tried; whatever is left is terminally flagged
                for r in remaining:
                    if r["id"] not in _blacklisted_ids(self.out):
                        self.blacklist(r["id"], "unresolved after full recovery ladder")
                break
        done = self.done_ids()
        remaining = self.remaining(done)
        self._write_status("done", load1, 0.0, plan, remaining)
        black = _blacklisted_ids(self.out)
        self._event("DONE: %d/%d valid, %d blacklisted, %d unresolved"
                    % (len(done & set(self.by_id)), len(self.rows), len(black & set(self.by_id)),
                       len(remaining)))
        return 0 if not remaining else 2

    def _sig(self, signum, frame):
        # FLAG-ONLY: no buffered IO, sleeps, or process work in the handler (a
        # reentrant stderr write raises RuntimeError and aborts shutdown). The main
        # loop's stopping branch does the actual draining against drain_deadline.
        if self.stopping:
            return
        self.stopping = True
        self.drain_deadline = time.time() + self.args.drain_secs
        try:
            os.write(2, b"\n  [supervisor] signal received: draining (deadline %ds)\n"
                     % self.args.drain_secs)
        except OSError:
            pass


def main(argv=None):
    cfg = load_ingest_config()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=load_source_root(),
                    help="source documents root (default $DOC2MD_SRC or [paths].source_docs)")
    ap.add_argument("--out", default=cfg.markdown_dir)
    ap.add_argument("--ocr", choices=["auto", "on", "off"], default="auto")
    ap.add_argument("--threads", type=int, default=0,
                    help="threads per worker (0 = [ingest].threads_per_shard)")
    ap.add_argument("--max-workers", type=int, default=0, help="hard cap on workers (0 = admission-bound)")
    ap.add_argument("--ramp-secs", type=int, default=int(os.environ.get("DOC2MD_RAMP_SECS", 120)),
                    help="workers younger than this reserve their full RAM headroom in "
                         "admission (MemAvailable hasn't seen their peak yet)")
    ap.add_argument("--tick", type=float, default=float(os.environ.get("DOC2MD_HEAL_TICK", 15)),
                    help="supervisor loop period, seconds")
    ap.add_argument("--stall-secs", type=int, default=int(os.environ.get("DOC2MD_STALL_SECS", 600)),
                    help="log-silence before the CPU-aware hang check")
    ap.add_argument("--busy-ticks", type=int, default=int(os.environ.get("DOC2MD_BUSY_TICKS", 50)),
                    help="min CPU ticks per 3s sample for a silent worker to be spared")
    ap.add_argument("--drain-secs", type=int, default=20,
                    help="grace period for workers to finish after SIGTERM/SIGINT")
    ap.add_argument("--max-respawns", type=int, default=50,
                    help="total extra worker spawns allowed (crash-loop backstop)")
    ap.add_argument("--worker-cmd", default=os.environ.get("DOC2MD_HEAL_WORKER_CMD", ""),
                    help="override the worker command (testing); default: this venv's docling_convert.py")
    ap.add_argument("--status-file", default="",
                    help="rollup JSON path (default <out>/_heal_status.json)")
    ap.add_argument("--status-only", action="store_true", default=False,
                    help="print the remaining/valid rollup and exit (no workers)")
    args = ap.parse_args(argv)
    if not args.src or not os.path.isdir(args.src):
        ap.error("source root not found (%r): pass --src, set $DOC2MD_SRC, or set "
                 "[paths].source_docs in config/default.local.toml" % (args.src,))
    if not args.status_file:
        args.status_file = os.path.join(args.out, "_heal_status.json")

    sup = Supervisor(args, cfg)
    if args.status_only:
        done = sup.done_ids()
        remaining = sup.remaining(done)
        black = _blacklisted_ids(args.out)
        print("docs=%d valid=%d remaining=%d blacklisted=%d claims=%d"
              % (len(sup.rows), len(done & set(sup.by_id)), len(remaining),
                 len(black & set(sup.by_id)), len(_claimed_ids(args.out))))
        for r in remaining[:20]:
            print("  remaining: %s" % r["rel"])
        return 0
    return sup.run()


if __name__ == "__main__":
    sys.exit(main())
