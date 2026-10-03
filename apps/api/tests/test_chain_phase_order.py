def test_first_subset_phases_first():
    """After decomposition, the "first" subset must keep its historic
    order: migrations, universe_sync, backfill_moex, bonds_depth,
    gap_recovery.

    fix/daily-bonds-depth restoration (2026-10-03) inserts ``bonds_depth``
    between ``backfill_moex`` and ``gap_recovery`` per the data-quality
    spec (lines 79-90 require bonds_depth to run AFTER backfill_moex
    and BEFORE corporate_actions; with the derived subset starting at
    corporate_actions, bonds_depth sits in the first subset, immediately
    after backfill_moex).
    """
    from algotrader_api.worker import _DAILY_CHAIN_FIRST_PHASES
    assert list(_DAILY_CHAIN_FIRST_PHASES) == [
        "migrations", "universe_sync", "backfill_moex", "bonds_depth",
        "gap_recovery",
    ]


def test_derived_subset_phases_last():
    """The "derived" subset must keep: corporate_actions, dividends,
    freshness_check, guardian."""
    from algotrader_api.worker import _DAILY_CHAIN_DERIVED_PHASES
    assert list(_DAILY_CHAIN_DERIVED_PHASES) == [
        "corporate_actions", "dividends",
        "freshness_check", "guardian",
    ]


def test_run_daily_chain_accepts_subset_flag(monkeypatch):
    import sys
    from algotrader_api import worker as worker_mod
    argv_default = sys.argv
    monkeypatch.setattr(sys, "argv", ["worker.py", "daily"])
    assert worker_mod._select_subset("daily") == "first"
    monkeypatch.setattr(
        sys, "argv", ["worker.py", "daily", "derived"]
    )
    assert worker_mod._select_subset("derived") == "derived"
    monkeypatch.setattr(sys, "argv", argv_default)