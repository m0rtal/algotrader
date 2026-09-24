def test_first_subset_phases_first():
    """After decomposition, the "first" subset must keep its historic
    order: migrations, universe_sync, backfill_moex, gap_recovery."""
    from algotrader_api.worker import _DAILY_CHAIN_FIRST_PHASES
    assert list(_DAILY_CHAIN_FIRST_PHASES) == [
        "migrations", "universe_sync", "backfill_moex", "gap_recovery",
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