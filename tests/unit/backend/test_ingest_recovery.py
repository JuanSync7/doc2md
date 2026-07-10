"""
title: Unit — failure classification + elastic scheduling policy
kind: tests
layer: backend
summary: classify_failure/recovery_action/plan_admission/order_todo drive the self-healing converter from plain values (no processes) — so they are unit-testable.
"""
# Pure policy, no disk/network/process. Tests go through the package public API.
from backend.ingest import (classify_failure, recovery_action, plan_admission, order_todo,
                            load_ingest_config,
                            FAIL_NONE, FAIL_OOM, FAIL_HANG, FAIL_DOCLING,
                            FAIL_TRANSIENT, FAIL_INPUT,
                            ACT_RETRY, ACT_ESCALATE, ACT_FALLBACK, ACT_BLACKLIST,
                            ADM_GROW, ADM_HOLD, ADM_SHRINK)

CFG = load_ingest_config()   # defaults are enough for policy tests (no env needed)


# --- classify_failure -------------------------------------------------------

def test_classify_watchdog_kill_is_hang_even_with_sigkill_code():
    # a watchdog kill uses SIGKILL(9) -> rc 137, but it is a HANG not an OOM
    assert classify_failure(returncode=137, watchdog_killed=True) == FAIL_HANG


def test_classify_bare_sigkill_is_oom():
    assert classify_failure(returncode=137) == FAIL_OOM        # 128+9
    assert classify_failure(returncode=-9) == FAIL_OOM         # subprocess -N form


def test_classify_oom_from_log_text():
    assert classify_failure(returncode=1, log_tail="terminate: std::bad_alloc") == FAIL_OOM
    assert classify_failure(returncode=1, log_tail="Out of memory: Killed process 123") == FAIL_OOM


def test_classify_input_errors_are_not_retried_as_transient():
    assert classify_failure(returncode=1, log_tail="Error: not a PDF") == FAIL_INPUT
    assert classify_failure(returncode=1, log_tail="file is password-protected") == FAIL_INPUT


def test_classify_docling_bad_status_and_empty_output():
    assert classify_failure(returncode=0, had_md=True, status="PARTIAL_SUCCESS") == FAIL_DOCLING
    assert classify_failure(returncode=0, had_md=True, status="FAILURE") == FAIL_DOCLING
    # clean exit but produced no markdown -> deterministic bad output
    assert classify_failure(returncode=0, had_md=False) == FAIL_DOCLING


def test_classify_success_and_transient():
    assert classify_failure(returncode=0, had_md=True, status="SUCCESS") == FAIL_NONE
    assert classify_failure(returncode=1) == FAIL_TRANSIENT     # generic non-zero, no signature


# --- recovery_action --------------------------------------------------------

def test_oom_escalates_to_bigger_solo_lane_then_falls_back():
    first = recovery_action(FAIL_OOM, attempts_used=0, cfg=CFG, cpu=32)
    assert first.action == ACT_ESCALATE
    assert first.threads == 32                       # big_doc_threads=0 -> all cores
    assert first.mem_gb == CFG.big_doc_mem_gb        # reserves the big lane's RAM
    # once escalation budget is spent, stop re-OOMing and use the fallback body
    after = recovery_action(FAIL_OOM, attempts_used=CFG.escalation_attempts, cfg=CFG, cpu=32)
    assert after.action == ACT_FALLBACK


def test_hang_also_escalates():
    assert recovery_action(FAIL_HANG, attempts_used=0, cfg=CFG, cpu=8).action == ACT_ESCALATE


def test_docling_bad_status_goes_straight_to_fallback():
    # re-running deterministic docling would give the same output -> don't retry it
    assert recovery_action(FAIL_DOCLING, attempts_used=0, cfg=CFG).action == ACT_FALLBACK


def test_input_is_blacklisted_immediately():
    assert recovery_action(FAIL_INPUT, attempts_used=0, cfg=CFG).action == ACT_BLACKLIST


def test_transient_retries_then_falls_back():
    assert recovery_action(FAIL_TRANSIENT, attempts_used=0, cfg=CFG).action == ACT_RETRY
    assert recovery_action(FAIL_TRANSIENT, attempts_used=CFG.retry_attempts,
                           cfg=CFG).action == ACT_FALLBACK


# --- plan_admission (good-citizen elastic control) --------------------------

def test_admission_grows_when_cores_and_ram_are_free():
    # 32 cores, 4 threads/shard, 2 workers running (8 cores), load low, plenty RAM
    p = plan_admission(loadavg1=2.0, mem_avail_gb=200, running_workers=2, cpu=32, cfg=CFG)
    assert p.action == ADM_GROW


def test_admission_shrinks_when_oversubscribed():
    # loadavg above high_frac*cpu (others spiked the box) -> yield a worker
    p = plan_admission(loadavg1=40, mem_avail_gb=200, running_workers=8, cpu=32, cfg=CFG)
    assert p.action == ADM_SHRINK


def test_admission_never_shrinks_below_one_worker():
    p = plan_admission(loadavg1=999, mem_avail_gb=200, running_workers=1, cpu=32, cfg=CFG)
    assert p.action != ADM_SHRINK


def test_admission_holds_when_ram_is_the_binding_constraint():
    # cores free + load low, but not enough RAM headroom for another shard -> hold
    p = plan_admission(loadavg1=2.0, mem_avail_gb=1, running_workers=2, cpu=32, cfg=CFG)
    assert p.action == ADM_HOLD


def test_admission_holds_when_cores_would_be_oversubscribed_by_growth():
    # 8 workers * 4 threads = 32 = cpu; adding one would exceed cores -> hold
    p = plan_admission(loadavg1=1.0, mem_avail_gb=500, running_workers=8, cpu=32, cfg=CFG)
    assert p.action == ADM_HOLD


# --- order_todo -------------------------------------------------------------

def test_order_todo_is_biggest_first_deterministic():
    items = [("small", 10), ("huge", 9000), ("mid", 500), ("tie_b", 500)]
    assert order_todo(items) == ["huge", "mid", "tie_b", "small"]   # ties by key


def test_order_todo_handles_missing_sizes():
    assert order_todo([("a", None), ("b", 5)]) == ["b", "a"]
