"""Route 5y windows to MOEX, 9m tail to Tinkoff, delisted to Tinkoff-fallback."""
import sqlite3
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

    def fake_moex_year_outcome(market, board, ticker, year,
                               last_trading_day=None):
        # Task 2: drive the new outcome-emitting fetcher so the
        # production class binding (which now exists next to
        # ``_fetch_year_moex``) is exercised. The bar list is the
        # same one the legacy stub would have returned; the outcome
        # is ``"error"`` so this bar-routing test does not assert
        # evidence (it never did). The evidence path is intentionally
        # fail-closed: legacy bar-only stubs do not produce evidence
        # because the upstream feed was not validated through the
        # new contract.
        return (fake_moex_year(market, board, ticker, year,
                               last_trading_day=last_trading_day),
                "error")

    async def fake_tinkoff(figi, date_from, date_to, interval):
        tinkoff_calls.append((date_from, date_to))
        return []

    runner._fetch_year_moex = staticmethod(fake_moex_year)
    runner._fetch_year_moex_outcome = staticmethod(fake_moex_year_outcome)
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

    def fake_moex_year_outcome(market, board, ticker, year,
                               last_trading_day=None):
        # Task 2: drive the new outcome-emitting fetcher. Bar list
        # empty (Tinkoff-only routing); outcome is "error" so the
        # evidence path is fail-closed (this test does not assert
        # evidence).
        return (fake_moex_year(market, board, ticker, year,
                               last_trading_day=last_trading_day),
                "error")

    async def fake_tinkoff(figi, date_from, date_to, interval):
        tinkoff_calls.append((date_from, date_to))
        return [{
            "ts": "2026-09-15", "open": 1, "high": 1, "low": 1,
            "close": 1, "volume": 1, "source": "tinkoff",
        }]

    runner._fetch_year_moex = staticmethod(fake_moex_year)
    runner._fetch_year_moex_outcome = staticmethod(fake_moex_year_outcome)
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

    def empty_moex_outcome(market, board, ticker, year,
                           last_trading_day=None):
        # Task 2: drive the new outcome-emitting fetcher. Empty
        # bar list; outcome is "error" (fail-closed; this test
        # does not assert evidence).
        return (empty_moex(market, board, ticker, year,
                           last_trading_day=last_trading_day),
                "error")

    runner._fetch_year_moex = staticmethod(empty_moex)
    runner._fetch_year_moex_outcome = staticmethod(empty_moex_outcome)

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

    def boom_moex_outcome(market, board, ticker, year,
                          last_trading_day=None):
        # Task 2: drive the new outcome-emitting fetcher. Propagate
        # the underlying exception so the walker logs the same
        # "moex year {year} failed" line the test asserts on. The
        # walker's existing ``except Exception`` branch converts the
        # exception into ``outcome = "error"`` and an empty bar
        # list; that is the fail-closed contract.
        return boom_moex(market, board, ticker, year,
                         last_trading_day=last_trading_day)

    runner._fetch_year_moex = staticmethod(boom_moex)
    runner._fetch_year_moex_outcome = staticmethod(boom_moex_outcome)
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

    runner._fetch_year_moex_outcome = staticmethod(partial_outcome)
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

    runner._fetch_year_moex_outcome = staticmethod(complete_outcome)
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


def test_walker_evidence_failure_does_not_undo_bars(runner):
    """R1: when the evidence helper fails (e.g. ``WriterLockBusy``)
    after the bar write has already committed, the bar write
    MUST NOT be rolled back. The walker swallows the evidence
    failure (logs ``moex_historical_evidence_deferred``) and
    keeps the bar list intact. This pins the "evidence failure
    doesn't undo committed bars" invariant.
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
        "VALUES ('GAZP', '00000000-0000-0000-0000-000000000997', "
        "'share', 'Gazp', 'rub', 10, 'RU0007661625')"
    )
    con.commit()
    con.close()
    from algotrader_api.ingestion import no_trade_evidence as _nte
    _nte.fetch_issuer_identity = lambda ticker: {
        "board": "TQBR", "isin": "RU0007661625",
    }

    # One business date zero-trade row + one real bar. The bar
    # write goes through ``replace_bars_for_figi`` (committed);
    # the evidence call goes through
    # ``record_historical_no_trade_evidence`` which acquires
    # the evidence lock.
    real_bars = [{
        "figi": None, "ts": "2024-06-03", "open": 100.0, "high": 102.0,
        "low": 99.0, "close": 101.0, "volume": 1000, "source": "moex",
        "_secid": "GAZP", "_boardid": "TQBR",
        "_numtrades": 5, "_value": 100000,
    }]

    def complete_outcome(market, board, ticker, year,
                         last_trading_day=None):  # noqa: ARG001
        return (list(real_bars), "complete")

    runner._fetch_year_moex_outcome = staticmethod(complete_outcome)
    from unittest.mock import AsyncMock
    runner.client.get_candles = AsyncMock(return_value=[])

    # Force the evidence writer lock to be busy. The bar write
    # uses the bar-writer lock (different role/phase), so it
    # still acquires — only the evidence write fails.
    from algotrader_api.ingestion import writer_lock as _wl_mod
    from algotrader_api.ingestion.writer_lock import WriterLockBusy

    real_lock = _wl_mod.writer_lock

    @_wl_mod.contextmanager  # type: ignore[attr-defined]
    def _busy_evidence_lock(db_path, **kw):
        if kw.get("role") == "no-trade-evidence" and kw.get("phase") == "evidence":
            raise WriterLockBusy(
                role=kw["role"], phase=kw["phase"],
                database_path=str(db_path),
                lock_path=str(db_path) + ".writer.lock",
                timeout_seconds=kw.get("timeout_seconds", 0.0),
                reason="test-forced-busy",
            )
        with real_lock(db_path, **kw):
            yield

    import unittest.mock as _mock
    with _mock.patch.object(_wl_mod, "writer_lock", _busy_evidence_lock):
        import asyncio
        # The walker swallows the WriterLockBusy from the
        # evidence call (logs ``moex_historical_evidence_deferred``)
        # and returns the bar count.
        n_bars = asyncio.run(runner._backfill_one(
            figi="00000000-0000-0000-0000-000000000997",
            ticker="GAZP",
            from_=date(2024, 1, 1),
            to=date(2024, 12, 31),
            source="moex",
        ))

    con = sqlite3.connect(runner.db_path)
    n_bars_db = con.execute(
        "SELECT COUNT(*) FROM bars "
        "WHERE figi='00000000-0000-0000-0000-000000000997' "
        "AND ts='2024-06-03'"
    ).fetchone()[0]
    n_evidence_db = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence "
        "WHERE figi='00000000-0000-0000-0000-000000000997'"
    ).fetchone()[0]
    con.close()
    assert n_bars == 1, (
        f"bar write must still report 1 even when evidence fails, "
        f"got n_bars={n_bars}"
    )
    assert n_bars_db == 1, (
        f"bar row must be committed before evidence failure, "
        f"got n_bars_db={n_bars_db}"
    )
    assert n_evidence_db == 0, (
        f"evidence was busy and must NOT have written, "
        f"got n_evidence_db={n_evidence_db}"
    )


def test_public_backfill_from_moex_persists_evidence_for_complete_outcome(
    tmp_path,
):
    """R1: the public ``BackfillRunner.backfill_from_moex(...)``
    walker (not just ``_backfill_one``) MUST drive the historical
    evidence integration end-to-end. With a real-looking
    ``_fetch_year_moex_outcome`` stub returning ``(zero_rows,
    "complete")`` and the upstream ISIN matching the stored ISIN,
    the public walker persists one ``moex_no_trade_evidence``
    row for the right figi, session_date, board, and isin.

    Bounded: 1 figi, 1 year, 1 zero-trade row on a Monday
    (business date). Uses the real ``backfill_from_moex``
    public method.
    """
    from algotrader_api.db import sqlite as _sqlitedb
    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    db_file = str(tmp_path / "public_walker.db")
    _sqlitedb.run_migrations(db_file, migrations_dir)
    _sqlitedb.close_all()
    figi = "BBG00-PUBLIC-WALKER"
    ticker = "GAZP"
    isin = "RU0007661625"
    con = sqlite3.connect(db_file)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES (?, ?, 'share', 'Gazp', 'rub', 10, ?)",
        (ticker, figi, isin),
    )
    con.commit()
    con.close()

    # Stub the public walker fetcher at the class level so the
    # walker sees a real-looking ``(rows, "complete")`` outcome
    # without HTTP. The single zero-trade row is on a Monday
    # (2024-01-15) — a valid business date.
    zero_row = [{
        "figi": None, "ts": "2024-01-15", "open": None, "high": None,
        "low": None, "close": None, "volume": 0, "source": "moex",
        "_secid": ticker, "_boardid": "TQBR",
        "_numtrades": 0, "_value": 0,
    }]

    def _outcome(market, board, tk, year, *, last_trading_day=None):  # noqa: ARG001
        return (list(zero_row), "complete")

    # Stub the metadata probe so the walker resolves to a
    # MOEX-routable figi without HTTP. The meta dict carries the
    # upstream ISIN; the helper compares it against the stored
    # ``instruments.isin`` and the two MUST match.
    def _get_meta(ticker, today, *, meta_cache, meta_lock):  # noqa: ARG001
        return {
            "market": "shares", "board": "TQBR",
            "listed_from": "2024-01-01",
            "listed_till": today.isoformat(),
            "isin": isin,
        }

    # Stub the universe discovery + meta prefetch so the public
    # walker does not try to call the broker.
    async def _noop_self(self, instruments):  # noqa: ARG001
        return None
    async def _noop_discover(self):  # noqa: ARG001
        return 1

    import algotrader_api.ingestion.backfill as _backfill_mod
    BackfillRunner._fetch_year_moex_outcome = staticmethod(_outcome)
    BackfillRunner._get_meta_moex = staticmethod(_get_meta)
    BackfillRunner.prefetch_moex_meta = _noop_self
    BackfillRunner._discover_universe = _noop_discover

    async def _noop_sink(_ev):
        return None

    try:
        runner = BackfillRunner(
            client=MagicMock(), db_path=db_file,
            event_sink=_noop_sink, run_id=0,
        )
        # Drive the real public walker.
        import asyncio as _asyncio
        _asyncio.run(
            runner.backfill_from_moex(today=date(2024, 12, 31)),
        )

        con2 = sqlite3.connect(db_file)
        try:
            evidence_rows = con2.execute(
                "SELECT figi, session_date, board, isin "
                "FROM moex_no_trade_evidence WHERE figi = ? "
                "ORDER BY session_date",
                (figi,),
            ).fetchall()
        finally:
            con2.close()
        assert evidence_rows == [
            (figi, "2024-01-15", "TQBR", isin),
        ], (
            f"public walker must persist 1 evidence row for "
            f"complete-outcome fetch, got {evidence_rows!r}"
        )
    finally:
        # Restore the class bindings so other tests in the run
        # are not affected by our monkeypatch.
        BackfillRunner._fetch_year_moex_outcome = staticmethod(
            _backfill_mod._fetch_year_moex_outcome,
        )
        BackfillRunner._get_meta_moex = staticmethod(
            _backfill_mod._get_meta_moex,
        )
