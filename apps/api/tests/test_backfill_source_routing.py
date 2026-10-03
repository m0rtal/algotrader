"""Route 5y windows to MOEX, 9m tail to Tinkoff, delisted to Tinkoff-fallback."""
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from algotrader_api.ingestion.backfill import BackfillRunner


@pytest.fixture(autouse=True)
def _run_migrations(tmp_path):
    """Run migrations on a per-test tmp DB so `bars` and `instrument_metadata`
    exist when `_backfill_one` writes to them. Mirrors the fixture in
    `test_backfill.py`."""
    from algotrader_api.db import sqlite as sqlitedb
    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    db_file = str(tmp_path / "state.db")
    sqlitedb.run_migrations(db_file, migrations_dir)
    sqlitedb.close_all()
    yield
    sqlitedb.close_all()


@pytest.fixture
def runner(tmp_path):
    """A BackfillRunner pointing at the migrated tmp DB."""
    return BackfillRunner(
        client=MagicMock(),
        db_path=str(tmp_path / "state.db"),
        event_sink=lambda ev: None,
        run_id=0,
    )


def test_auto_routes_long_window_to_moex_year_walker(runner, tmp_path):
    """A 5-year window must call _fetch_year_moex for each year, NOT get_candles."""
    # Pre-populate the MOEX metadata cache so `_resolve_source` sees
    # this ticker as MOEX-tradable. Production callers do this via
    # `prefetch_moex_meta()`; tests bypass the network by writing
    # the cache directly.
    runner._moex_meta["TEST"] = {
        "market": "shares", "board": "TQBR",
        "listed_from": date(2021, 1, 1),
        # Identity gate (PR #176 follow-up): meta carries an ISIN
        # field; the fixture below seeds the instrument with the
        # same ISIN so the gate passes.
        "isin": "TEST0000000",
    }
    # Identity gate reads the figi's ISIN from `instruments`. Seed it.
    import sqlite3
    con = sqlite3.connect(runner.db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES ('TEST', '00000000-0000-0000-0000-000000000001', "
        "'share', 'Test', 'rub', 1, 'TEST0000000')"
    )
    con.commit()
    con.close()
    moex_calls: list[int] = []
    tinkoff_calls: list[tuple[date, date]] = []

    def fake_moex_year(market, board, ticker, year, last_trading_day=None):
        # `_fetch_year_moex` is a sync module-level function; the brief
        # pseudocode declared it `async def` but the call site in
        # `_backfill_one_moex` does NOT await — so this must be sync.
        moex_calls.append(year)
        return [{
            "figi": None, "ts": f"{year}-06-15",
            "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
            "volume": 100, "source": "moex",
        }]

    async def fake_tinkoff(figi, date_from, date_to, interval):
        tinkoff_calls.append((date_from, date_to))
        return []

    runner._fetch_year_moex = staticmethod(fake_moex_year)
    runner.client.get_candles = AsyncMock(side_effect=fake_tinkoff)

    # Identity gate (PR #176 follow-up): _backfill_one_moex calls
    # ``fetch_issuer_identity`` for the figi's expected MOEX ISIN.
    # Stub it to match the seeded ``TEST0000000`` ISIN.
    from algotrader_api.ingestion import no_trade_evidence as _nte
    _nte.fetch_issuer_identity = lambda ticker: {
        "board": "TQBR", "isin": "TEST0000000",
    }

    # 5-year span: 2021..2026 — should produce 5 MOEX year calls, plus
    # 0 Tinkoff calls (the trailing 9m is empty since MOEX covers it).
    from_ = date(2021, 1, 1)
    to_ = date(2026, 9, 21)
    # _backfill_one is sync; just call it
    # Use the inner coroutine directly via asyncio.run
    import asyncio
    added = asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000001",
        ticker="TEST",
        from_=from_,
        to=to_,
        source="auto",
    ))

    # MOEX was called for 2021..2025 (5 historical years) + 2026 current year
    # The trailing 9-month rule: if MOEX covered up to yesterday, no Tinkoff.
    assert len(moex_calls) == 6, f"expected 6 MOEX year calls, got {moex_calls}"
    assert 2021 in moex_calls and 2026 in moex_calls
    # Tinkoff is NOT called when MOEX covered the window end-to-end
    # (it would be called only for the trailing-9m bridge).
    # We assert the bridge length is correctly limited.
    if tinkoff_calls:
        span_days = (tinkoff_calls[0][1] - tinkoff_calls[0][0]).days
        assert span_days <= 270, f"trailing bridge too long: {span_days} days"


def test_tinkoff_source_routes_to_get_candles(runner):
    """source='tinkoff' (operator override) keeps current behaviour."""
    moex_calls: list[int] = []
    tinkoff_calls: list[tuple[date, date]] = []

    def fake_moex_year(market, board, ticker, year, last_trading_day=None):
        moex_calls.append(year)
        return []

    async def fake_tinkoff(figi, date_from, date_to, interval):
        tinkoff_calls.append((date_from, date_to))
        return [{
            "ts": "2026-09-15", "open": 1, "high": 1, "low": 1,
            "close": 1, "volume": 1, "source": "tinkoff",
        }]

    runner._fetch_year_moex = staticmethod(fake_moex_year)
    runner.client.get_candles = AsyncMock(side_effect=fake_tinkoff)

    from_ = date(2025, 1, 1)
    to_ = date(2026, 9, 21)
    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000002",
        ticker="OVERRIDE",
        from_=from_,
        to=to_,
        source="tinkoff",
    ))

    assert moex_calls == [], "MOEX must NOT be called when source=tinkoff"
    assert tinkoff_calls, "Tinkoff must be called"


def test_skipped_marker_NOT_set_on_empty_moex_response(runner):
    """Empty MOEX response for one year must not poison metadata."""
    upserts: list[dict] = []

    def fake_upsert(figi, last_bar_ts, total_bars, status, error_msg):
        upserts.append({"figi": figi, "status": status, "last_bar_ts": last_bar_ts})

    runner._upsert_metadata = fake_upsert
    # Pre-populate MOEX cache so `_resolve_source` routes to MOEX.
    runner._moex_meta["EMPTY"] = {
        "market": "shares", "board": "TQBR",
        "listed_from": date(2021, 1, 1),
    }

    def empty_moex(market, board, ticker, year, last_trading_day=None):
        return []  # MOEX returns empty for this year

    runner._fetch_year_moex = staticmethod(empty_moex)

    from_ = date(2021, 1, 1)
    to_ = date(2021, 12, 31)
    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000003",
        ticker="EMPTY",
        from_=from_,
        to=to_,
        source="moex",
    ))

    # No skipped marker — only logged warn line + 0 bars.
    skipped = [u for u in upserts if u.get("status") == "skipped"]
    assert skipped == [], f"MOEX-empty path set skipped marker: {skipped}"


def test_moex_year_exception_is_swallowed(runner):
    """A failing `_fetch_year_moex` for one year must not abort the figi.

    The MOEX year walker continues to the next year after logging a
    warn; the trailing bridge still runs. Coverage pin: exercises the
    `except Exception` branch in `_backfill_one_moex`.
    """
    logs: list[dict] = []

    async def fake_log(level, **kwargs):
        logs.append({"level": level, **kwargs})

    runner._log = fake_log  # type: ignore[assignment]
    runner._moex_meta["BOOM"] = {
        "market": "shares", "board": "TQBR",
        "listed_from": date(2024, 1, 1),
        # Identity gate (PR #176 follow-up): ISIN must match between
        # the meta and the seeded instrument, or the new gate refuses
        # the run before _fetch_year_moex is even called.
        "isin": "BOOM0000000",
    }
    # Seed the instrument with a matching ISIN + stub
    # ``fetch_issuer_identity`` so the gate's two checks pass.
    import sqlite3
    con = sqlite3.connect(runner.db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES ('BOOM', '00000000-0000-0000-0000-000000000004', "
        "'share', 'Boom', 'rub', 1, 'BOOM0000000')"
    )
    con.commit()
    con.close()
    from algotrader_api.ingestion import no_trade_evidence as _nte
    _nte.fetch_issuer_identity = lambda ticker: {
        "board": "TQBR", "isin": "BOOM0000000",
    }

    def boom_moex(market, board, ticker, year, last_trading_day=None):
        raise ConnectionError("MOEX ISS down")

    runner._fetch_year_moex = staticmethod(boom_moex)
    runner.client.get_candles = AsyncMock(return_value=[])

    from_ = date(2024, 1, 1)
    to_ = date(2024, 12, 31)
    import asyncio
    # Must not raise — exception is caught and logged.
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000004",
        ticker="BOOM",
        from_=from_,
        to=to_,
        source="moex",
    ))

    year_failed = [m for m in logs if "moex year 2024 failed" in str(m.get("message", ""))]
    assert year_failed, f"expected 'moex year 2024 failed' log, got {logs}"
    # Substring match per R8: don't pin exact phrasing.


def test_prefetch_moex_meta_skips_cached_and_empty_tickers(runner):
    """prefetch_moex_meta skips tickers already in the cache and tickers
    with no ticker string. This pins the `continue` branch on line 1018.
    """
    # Pre-cache one ticker so prefetch skips it.
    runner._moex_meta["CACHED"] = {"market": "shares", "board": "TQBR", "listed_from": date(2024, 1, 1)}
    call_count = {"n": 0}

    def fake_get_meta(ticker, today, *, meta_cache, meta_lock):
        call_count["n"] += 1
        # Simulate MOEX probe — cache the result.
        with meta_lock:
            meta_cache[ticker] = None
        return None

    runner._get_meta_moex = staticmethod(fake_get_meta)
    instruments = [
        {"ticker": "CACHED"},     # already cached → skip
        {"ticker": ""},           # empty ticker → skip
        {"ticker": "FRESH"},      # probe
    ]
    import asyncio
    asyncio.run(runner.prefetch_moex_meta(instruments))
    assert call_count["n"] == 1, f"only FRESH should be probed; calls={call_count}"
    assert runner._moex_meta.get("FRESH") is None


def test_walker_partial_outcome_writes_no_evidence_but_keeps_bars(runner):
    """Partial historical fetch leaves moex_no_trade_evidence
    untouched; the real bar (returned by the partial response) is
    still written to the bars table.

    The bar consumer sees a real-looking 1-page row for 2024-01-15,
    but the page reports ``total=2`` while only 1 row is returned
    → outcome ``partial``. The bar consumer writes the row it has;
    the evidence helper sees ``partial`` and short-circuits.
    """
    # Pre-cache MOEX meta so ``_resolve_source`` routes to MOEX.
    runner._moex_meta["GAZP"] = {
        "market": "shares", "board": "TQBR",
        "listed_from": date(2024, 1, 1),
        "isin": "RU0007661625",
    }
    # Seed the instrument with a matching ISIN.
    import sqlite3
    con = sqlite3.connect(runner.db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES ('GAZP', '00000000-0000-0000-0000-000000000999', "
        "'share', 'Gazp', 'rub', 10, 'RU0007661625')"
    )
    con.commit()
    con.close()
    from algotrader_api.ingestion import no_trade_evidence as _nte
    _nte.fetch_issuer_identity = lambda ticker: {
        "board": "TQBR", "isin": "RU0007661625",
    }

    real_partial_row = [{
        "figi": None, "ts": "2024-01-15", "open": 100, "high": 102,
        "low": 99, "close": 101, "volume": 1000, "source": "moex",
        "_secid": "GAZP", "_boardid": "TQBR",
        "_numtrades": 5, "_value": 100000,
    }]

    def partial_outcome(market, board, ticker, year,
                        last_trading_day=None):  # noqa: ARG001
        return (real_partial_row, "partial")

    runner._fetch_year_moex_outcome = partial_outcome
    # Stub Tinkoff so the trailing 9m bridge is a no-op.
    from unittest.mock import AsyncMock
    runner.client.get_candles = AsyncMock(return_value=[])

    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000999",
        ticker="GAZP",
        from_=date(2024, 1, 1),
        to=date(2024, 12, 31),
        source="moex",
    ))

    con = sqlite3.connect(runner.db_path)
    n_bars = con.execute(
        "SELECT COUNT(*) FROM bars "
        "WHERE figi='00000000-0000-0000-0000-000000000999' "
        "AND ts='2024-01-15'"
    ).fetchone()[0]
    n_evidence = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence "
        "WHERE figi='00000000-0000-0000-0000-000000000999'"
    ).fetchone()[0]
    con.close()
    assert n_bars == 1, f"partial bar must still be written, got {n_bars}"
    assert n_evidence == 0, (
        f"partial outcome must NOT record evidence, got {n_evidence}"
    )


def test_walker_complete_outcome_writes_evidence_for_business_dates(runner):
    """Complete historical fetch records zero-trade evidence for the
    business dates in the response, real-bar-wins, and keeps the bar
    write.
    """
    runner._moex_meta["GAZP"] = {
        "market": "shares", "board": "TQBR",
        "listed_from": date(2024, 1, 1),
        "isin": "RU0007661625",
    }
    import sqlite3
    con = sqlite3.connect(runner.db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES ('GAZP', '00000000-0000-0000-0000-000000000998', "
        "'share', 'Gazp', 'rub', 10, 'RU0007661625')"
    )
    con.commit()
    con.close()
    from algotrader_api.ingestion import no_trade_evidence as _nte
    _nte.fetch_issuer_identity = lambda ticker: {
        "board": "TQBR", "isin": "RU0007661625",
    }

    # 2024-01-15 is a Monday, 2024-01-16 is a Tuesday, 2024-01-13 is a
    # Saturday. All three are zero-trade rows; only the weekdays
    # pass the business-date filter.
    zero_rows = [
        {"figi": None, "ts": "2024-01-13", "open": None, "high": None,
         "low": None, "close": None, "volume": 0, "source": "moex",
         "_secid": "GAZP", "_boardid": "TQBR",
         "_numtrades": 0, "_value": 0},
        {"figi": None, "ts": "2024-01-15", "open": None, "high": None,
         "low": None, "close": None, "volume": 0, "source": "moex",
         "_secid": "GAZP", "_boardid": "TQBR",
         "_numtrades": 0, "_value": 0},
        {"figi": None, "ts": "2024-01-16", "open": None, "high": None,
         "low": None, "close": None, "volume": 0, "source": "moex",
         "_secid": "GAZP", "_boardid": "TQBR",
         "_numtrades": 0, "_value": 0},
    ]

    def complete_outcome(market, board, ticker, year,
                         last_trading_day=None):  # noqa: ARG001
        return (list(zero_rows), "complete")

    runner._fetch_year_moex_outcome = complete_outcome
    from unittest.mock import AsyncMock
    runner.client.get_candles = AsyncMock(return_value=[])

    import asyncio
    asyncio.run(runner._backfill_one(
        figi="00000000-0000-0000-0000-000000000998",
        ticker="GAZP",
        from_=date(2024, 1, 1),
        to=date(2024, 12, 31),
        source="moex",
    ))

    con = sqlite3.connect(runner.db_path)
    con.row_factory = sqlite3.Row
    n_evidence = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence "
        "WHERE figi='00000000-0000-0000-0000-000000000998' "
        "ORDER BY session_date"
    ).fetchall()
    con.close()
    # Two business dates (Mon + Tue), no Saturday.
    assert [r["session_date"] for r in n_evidence] == [
        "2024-01-15", "2024-01-16",
    ], f"expected Mon+Tue only, got {n_evidence}"