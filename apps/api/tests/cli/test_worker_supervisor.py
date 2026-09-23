"""Regression test for PR #123: STUCK_AT_STARTUP watchdog rule removed.

Bug (2026-09-23):
  The watchdog inside ``scripts/algotrader-supervisor.sh`` had a
  ``STUCK_AT_STARTUP`` rule: ``if elapsed > 600 and bars_growth == 0``,
  the supervisor killed the worker. With the post-PR-#120 / #122
  backfill cycle taking ~30 min per run (8 min prefetch + ~22 min
  ``_process_one_bounded`` walking 3800 figis at Semaphore(5)), the
  600 s threshold fired ~5 min before any bar could be written. The
  worker was killed mid-cycle, restart took another 8 min, and the
  loop never converged.

Fix:
  Removed the ``STUCK_AT_STARTUP`` rule. Heartbeat freshness is the
  right liveness signal: ``HEARTBEAT_MAX_AGE_DAYS=0.0208`` (30 min)
  triggers ``STALL`` if the worker truly stops responding. Bar-
  progress is communicated via the heartbeat age + bar count which
  the operator already monitors (the original 2018-12 design
  comment in the supervisor script explicitly justified removing
  such heuristics because of false positives).

These tests guard the rule by source inspection. Behavioural test
(running the supervisor and observing the kill) is brittle because
it requires a live worker + database, and the bug only manifests
under real load. The source-level guard catches a regression
immediately.
"""
from __future__ import annotations

import re
from pathlib import Path


SUPERVISOR_SCRIPT = (
    Path(__file__).parent.parent.parent.parent.parent
    / "scripts" / "algotrader-supervisor.sh"
)


def _read_supervisor() -> str:
    assert SUPERVISOR_SCRIPT.exists(), (
        f"supervisor script missing at {SUPERVISOR_SCRIPT}"
    )
    return SUPERVISOR_SCRIPT.read_text()


def test_stuck_at_startup_rule_removed():
    """The STUCK_AT_STARTUP rule must not be present.

    PR #123 removed the ``if elapsed > 600 and bars_growth == 0``
    clause that killed the worker 5 min into a 30-min backfill cycle.
    If this rule reappears, the test fails so the regression is
    caught before merge.
    """
    src = _read_supervisor()
    # Look for the rule pattern. We accept the literal text only —
    # not a comment that mentions it (the new comment in the script
    # references STUCK_AT_STARTUP as historical context).
    rule_pattern = re.compile(
        r"^\s*elif\s+elapsed\s*>\s*600\s+and\s+bars_growth\s*==\s*0",
        re.MULTILINE,
    )
    match = rule_pattern.search(src)
    assert match is None, (
        "STUCK_AT_STARTUP rule has been re-introduced in "
        f"scripts/algotrader-supervisor.sh at offset {match.start()}. "
        "This rule kills the worker ~5 min into a 30-min backfill "
        "cycle (after PR #120 fixed asyncio and PR #122 fixed "
        "prefetch). See PR #123 for the rationale. The right liveness "
        "signal is heartbeat freshness (HEARTBEAT_MAX_AGE_DAYS), not "
        "elapsed time without bar progress."
    )


def test_stall_rule_still_present():
    """The STALL rule (heartbeat-freshness based) must still exist."""
    src = _read_supervisor()
    assert "STALL" in src, (
        "Heartbeat-freshness STALL rule must remain — it is the right "
        "liveness signal. The watchdog should still kill when the "
        "worker's heartbeat is stale (HEARTBEAT_MAX_AGE_DAYS exceeded)."
    )
    assert "hb_age_days > hb_max" in src, (
        "STALL rule predicate missing — must check heartbeat age "
        "against HEARTBEAT_MAX."
    )


def test_kill_path_still_works():
    """The kill-on-STALL path must still exist (grep on the watchdog output)."""
    src = _read_supervisor()
    assert 'grep -qE "STALL|STUCK_AT_STARTUP"' in src, (
        "Kill path on watchdog STALL output missing. The supervisor "
        "must still kill the worker PID when the watchdog emits STALL."
    )


def test_watchdog_diagnostic_still_logged():
    """The watchdog must still emit its diagnostic line every 30s."""
    src = _read_supervisor()
    assert "watchdog check:" in src, (
        "The supervisor's every-30s diagnostic log line is missing. "
        "Without it the operator cannot observe heartbeat age, bar age, "
        "and bar growth while the worker runs."
    )
