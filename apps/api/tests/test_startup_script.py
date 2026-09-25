"""Tests for scripts/startup-algotrader.sh sequential ordering.

Spec: openspec/changes/db-bootstrap-hardening/specs/data-quality/spec.md
(Requirement: Sequential Bootstrap Ordering)
"""
from pathlib import Path

import pytest

SCRIPT = Path("/home/hermes/algotrader/scripts/startup-algotrader.sh")
CONTENTS = SCRIPT.read_text()


def test_script_waits_for_api_before_starting_workers():
    """API supervisor start must precede any worker supervisor start.
    The wait_for_api_health call must sit between them."""
    api_idx = CONTENTS.index("algotrader-api-supervisor.sh")
    worker_idx = CONTENTS.index("algotrader-supervisor.sh algotrader-moex-backfill")
    wait_idx = CONTENTS.index("wait_for_api_health")
    assert api_idx < wait_idx < worker_idx, (
        f"Order violated: api={api_idx}, wait={wait_idx}, worker={worker_idx}"
    )


def test_script_uses_health_endpoint():
    """The script must poll /health (not some other endpoint)."""
    assert "/health" in CONTENTS


def test_script_polls_health_with_backoff():
    """The script must poll with a max attempt count AND a sleep
    between attempts. Hard-coded retry budget."""
    assert "max_attempts=30" in CONTENTS
    assert "sleep 1" in CONTENTS