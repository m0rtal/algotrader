"""Real-script tests for apps/api/scripts/catchup_same_day.py.

Exercises ``script.main()`` and the real ``replace_bars_for_figi``
writer against a fully-migrated SQLite. Only the HTTP-bound helpers
(``_get_meta_moex``, ``_fetch_moex_range``, ``fetch_issuer_identity``)
and the wall clock are stubbed — the script and the writer are real.

Pins: matching RU ISIN receives the same-day bar; US mirror (different
ISIN) is skipped; missing-instrument-ISIN, failed identity probe,
board mismatch, wrong raw SECID/BOARDID, NULL OHLC never write; failed
probe stays retryable; second run is stable; time guard is preserved.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sqlite3
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# Per-symbol ISINs. RU000A107UL4 = real Russian share; US00206R1023 =
# real US AT&T. Both share the ticker "T" by construction (the brief's
# verified reproduction case). The RU figi in tests must carry the RU
# ISIN, the US figi the US ISIN — that is what the script must check.
RU_ISIN = "RU000A107UL4"
US_ISIN = "US00206R1023"
RU_FIGI = "BBG00RU000A1"
US_FIGI = "BBG00US00002"

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "catchup_same_day.py"


# ── script import (re-runs per test so monkeypatching stays per-test) ──


def _load_script():
    spec = importlib.util.spec_from_file_location("catchup_same_day_under_test", str(SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, sys.modules["algotrader_api.ingestion.backfill"], sys.modules[
        "algotrader_api.ingestion.no_trade_evidence"
    ]


def _run_main(script, argv: list[str]) -> int:
    saved = sys.argv
    sys.argv = ["catchup_same_day.py", *argv]
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return script.main()
    finally:
        sys.argv = saved


# ── per-test seeding on top of fresh_db's migrated schema ──


def _seed_instruments(db_path: str, *, rows: list[tuple[str, str, str | None]],
                      session: date) -> None:
    """Seed instruments + a 'recent enough' bars row per figi.

    The script picks active instruments as ``instruments.isin NOT NULL
    AND has a bar within the last 10 calendar days``, so we drop one
    bars row per figi dated ``session - 1``.
    """
    con = sqlite3.connect(db_path)
    last_ts = (session - timedelta(days=1)).isoformat()
    for figi, ticker, isin in rows:
        con.execute(
            "INSERT OR REPLACE INTO instruments "
            "(ticker, figi, class, name, currency, lot_size, isin) "
            "VALUES (?, ?, 'share', ?, 'RUB', 1, ?)",
            (ticker, figi, ticker, isin),
        )
        con.execute(
            "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
            "VALUES (?, ?, 100, 110, 95, 105, 1000)",
            (figi, last_ts),
        )
    con.commit()
    con.close()


def _bar_count(db_path: str, figi: str | None = None) -> int:
    con = sqlite3.connect(db_path)
    try:
        if figi is None:
            return con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
        return con.execute("SELECT COUNT(*) FROM bars WHERE figi = ?", (figi,)).fetchone()[0]
    finally:
        con.close()


def _stub_last_trading_day(monkeypatch, backfill, script, session: date):
    """Make the script's ``session`` look newer than the last trading day.

    The script binds ``_last_trading_day`` via ``from …backfill import``;
    patching only the ``backfill`` module attribute does not affect the
    bound name. Patch both.
    """
    prev = session - timedelta(days=1)

    def stub(today, db_path, **_):
        return prev

    monkeypatch.setattr(backfill, "_last_trading_day", stub)
    monkeypatch.setattr(script, "_last_trading_day", stub)


def _stub_http(monkeypatch, backfill, nte, script,
                meta_fn, identity_fn, fetch_fn):
    """Patch the three HTTP-bound helpers on BOTH the source module
    and the script module. The script imports them via
    ``from … import`` which rebinds the name in the script's namespace;
    patching only the source module leaves the script calling the real
    (network-bound) function. Tests must be hermetic — no real HTTP."""
    monkeypatch.setattr(backfill, "_get_meta_moex", meta_fn)
    monkeypatch.setattr(nte, "fetch_issuer_identity", identity_fn)
    monkeypatch.setattr(backfill, "_fetch_moex_range", fetch_fn)
    monkeypatch.setattr(script, "_get_meta_moex", meta_fn)
    monkeypatch.setattr(script, "fetch_issuer_identity", identity_fn)
    monkeypatch.setattr(script, "_fetch_moex_range", fetch_fn)


def _meta_ok(meta_cache, **_):
    """Board-only meta. Identity probe is the one that adds ISIN."""
    return {
        "market": "shares",
        "board": "TQBR",
        "listed_from": "2020-01-01",
        "listed_till": "2099-12-31",
    }


def _bar(ts: str, *, secid: str, boardid: str = "TQBR"):
    """MOEX-shaped dict, including the raw _secid/_boardid for verification."""
    return {
        "ts": ts,
        "open": 100.0,
        "high": 110.0,
        "low": 95.0,
        "close": 105.0,
        "volume": 1000,
        "_secid": secid,
        "_boardid": boardid,
        "_numtrades": 10,
        "_value": 105000,
    }


# ── tests ─────────────────────────────────────────────────────────────


def test_ru_isin_gets_bar_us_mirror_skipped(fresh_db, tmp_path, monkeypatch):
    """Two figis share ticker "T"; only the RU one (matching ISIN) gets the bar."""
    session = date(2026, 9, 28)
    _seed_instruments(
        fresh_db,
        rows=[(RU_FIGI, "T", RU_ISIN), (US_FIGI, "T", US_ISIN)],
        session=session,
    )

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    def fake_identity(ticker):
        return {"board": "TQBR", "isin": RU_ISIN}

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        return [_bar(session.isoformat(), secid=ticker)]

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 2  # seed + same-day
    assert _bar_count(fresh_db, US_FIGI) == 1, "US mirror must NOT receive the RU bar"


def test_missing_instrument_isin_counted_not_fetched(fresh_db, tmp_path, monkeypatch):
    """Instrument with NULL/empty ISIN never causes a fetch or write."""
    session = date(2026, 9, 28)
    _seed_instruments(
        fresh_db,
        rows=[("FIGI-NOISIN", "X", ""), ("FIGI-NULLISIN", "Y", None)],
        session=session,
    )

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    fetched: list[str] = []

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        fetched.append(ticker)
        return [_bar(session.isoformat(), secid=ticker)]

    def fake_identity(t):
        return {"board": "TQBR", "isin": RU_ISIN}

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert fetched == [], f"Instruments without ISIN must be skipped; got fetches for {fetched!r}"
    assert _bar_count(fresh_db) == 2, "Only seed bars; no same-day rows"


def test_failed_identity_probe_retries_next_run(fresh_db, tmp_path, monkeypatch):
    """A failed probe must not poison the cache; the next run retries."""
    session = date(2026, 9, 28)
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=session)
    cache_path = tmp_path / "cache.json"

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        return [_bar(session.isoformat(), secid=ticker)]

    calls = {"n": 0}

    def flaky_identity(ticker):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # upstream hiccup on first probe
        return {"board": "TQBR", "isin": RU_ISIN}

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, flaky_identity, fake_fetch)

    # Run 1: identity fails → no write.
    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(cache_path),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 1, "Failed probe must not write"

    cache_after = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    assert "T" not in cache_after, (
        f"Failed probe must NOT be cached as a miss; got cache={cache_after!r}"
    )

    # Run 2: probe succeeds → bar written.
    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(cache_path),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 2, "Run 2 must retry the probe and write"


def test_upstream_board_mismatch_rejects_identity(fresh_db, tmp_path, monkeypatch):
    """Cached board TQBR + upstream reports board SMAL → no write."""
    session = date(2026, 9, 28)
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=session)

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = {
            "market": "shares", "board": "TQBR",
            "listed_from": "2020-01-01", "listed_till": "2099-12-31",
        }
        return meta_cache[ticker]

    def fake_identity(ticker):
        # ISIN matches but board disagrees — cross-listed mirror leakage.
        return {"board": "SMAL", "isin": RU_ISIN}

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        return [_bar(session.isoformat(), secid=ticker, boardid=board)]

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 1, "Board mismatch must not produce a same-day bar"


def test_wrong_raw_secid_or_boardid_row_filtered(fresh_db, tmp_path, monkeypatch):
    """MOEX returns wrong _secid / _boardid → row dropped before write."""
    session = date(2026, 9, 28)
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=session)

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    def fake_identity(ticker):
        return {"board": "TQBR", "isin": RU_ISIN}

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        # Wrong SECID and wrong boardid in the same response.
        return [_bar(session.isoformat(), secid="OTHER", boardid="SMAL")]

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 1, "Wrong SECID/BOARDID row must not become a candle"


def test_second_run_does_not_duplicate_bars(fresh_db, tmp_path, monkeypatch):
    """Running twice on the same session keeps bar count stable (replace=False)."""
    session = date(2026, 9, 28)
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=session)
    cache_path = tmp_path / "cache.json"

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    def fake_identity(ticker):
        return {"board": "TQBR", "isin": RU_ISIN}

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        return [_bar(session.isoformat(), secid=ticker)]

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    for _ in range(2):
        rc = _run_main(script, [
            "--db", fresh_db, "--cache", str(cache_path),
            "--date", session.isoformat(), "--force", "--sleep", "0",
        ])
        assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 2, "replace=False must keep exactly 2 rows"


def test_null_ohlc_does_not_become_candle(fresh_db, tmp_path, monkeypatch):
    """NULL OHLC row must be dropped by the writer, not fabricated."""
    session = date(2026, 9, 28)
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=session)

    script, backfill, nte = _load_script()
    _stub_last_trading_day(monkeypatch, backfill, script, session)

    def fake_meta(ticker, yesterday, *, meta_cache, meta_lock):
        meta_cache[ticker] = _meta_ok(meta_cache)
        return meta_cache[ticker]

    def fake_identity(ticker):
        return {"board": "TQBR", "isin": RU_ISIN}

    def fake_fetch(market, board, ticker, from_d, to_d, *, last_trading_day=None):
        return [{
            "ts": session.isoformat(),
            "open": None, "high": None, "low": None, "close": None,
            "volume": 0,
            "_secid": ticker, "_boardid": "TQBR",
            "_numtrades": 0, "_value": 0,
        }]

    _stub_http(monkeypatch, backfill, nte, script, fake_meta, fake_identity, fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"),
        "--date", session.isoformat(), "--force", "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 1, "NULL-OHLC row must not become a candle"


def test_before_close_time_guard_preserved(fresh_db, tmp_path, monkeypatch):
    """Without --force before 16:15 UTC: script exits 0, no fetch."""
    _seed_instruments(fresh_db, rows=[(RU_FIGI, "T", RU_ISIN)], session=date.today())

    script, backfill, nte = _load_script()

    fixed_now = datetime(date.today().year, date.today().month, date.today().day,
                        12, 0, tzinfo=timezone.utc)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is None else fixed_now.astimezone(tz)

    monkeypatch.setattr(script, "datetime", FakeDT)

    def fake_fetch(*a, **k):
        raise AssertionError("fetch must not be called under the time guard")

    monkeypatch.setattr(backfill, "_fetch_moex_range", fake_fetch)
    monkeypatch.setattr(script, "_fetch_moex_range", fake_fetch)

    rc = _run_main(script, [
        "--db", fresh_db, "--cache", str(tmp_path / "cache.json"), "--sleep", "0",
    ])
    assert rc == 0
    assert _bar_count(fresh_db, RU_FIGI) == 1, "Seed bar only; no same-day write under guard"