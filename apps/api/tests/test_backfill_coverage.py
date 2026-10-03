"""Coverage tests for BackfillRunner.run() error paths and helpers."""

import asyncio
import importlib
import importlib.util
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import date, timedelta
from pathlib import Path

import pytest

from algotrader_api.ingestion import backfill as bf_mod
from algotrader_api.ingestion.backfill import BackfillRunner


def _seed_instruments(db_path: Path) -> None:
    con = sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE,
            class TEXT, name TEXT,
            currency TEXT, lot_size INTEGER, isin TEXT, sector TEXT,
            source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("SBER", "BBG001", "share", "Sber", "RUB", 1),
    )
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
        ("GAZP", "BBG002", "share", "Gazp", "RUB", 1),
    )
    con.commit()
    con.close()


def _seed_migrations(db_path: Path, migrations_dir: Path) -> None:
    from algotrader_api.db import sqlite as sqlitedb

    sqlitedb.run_migrations(str(db_path), str(migrations_dir))


class _BoomClient:
    """All SDK methods raise — exercises the unhandled-error paths."""

    async def get_shares(self):
        raise RuntimeError("boom shares")

    async def get_bonds(self):
        raise RuntimeError("boom bonds")

    async def get_etfs(self):
        raise RuntimeError("boom etfs")

    async def get_futures(self):
        raise RuntimeError("boom futures")

    async def get_options(self):
        raise RuntimeError("boom options")

    async def get_candles(self, **kw):
        raise RuntimeError("boom candles")

    async def aclose(self):
        pass


class _AlwaysBoomCandles:
    async def get_shares(self):
        return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]

    async def get_bonds(self):
        return []

    async def get_etfs(self):
        return []

    async def get_futures(self):
        return []

    async def get_options(self):
        return []

    async def get_candles(self, **kw):
        raise RuntimeError("boom candles")

    async def aclose(self):
        pass


def _migrations_dir() -> Path:
    # tests/test_backfill_coverage.py is at apps/api/tests/, so parent.parent = apps/api.
    return Path(__file__).resolve().parent.parent / "src" / "algotrader_api" / "db" / "migrations"


@pytest.mark.asyncio
async def test_run_universe_discovery_failure_emits_done_event(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(db),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    # Universe discovery raised on every SDK method → runner still
    # emits at least one 'done' event and tickers_done is 0.
    done_events = [e for e in events if e.type == "done"]
    assert len(done_events) >= 1
    assert runner.tickers_done == 0


@pytest.mark.asyncio
async def test_run_per_ticker_error_logs_and_continues(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_AlwaysBoomCandles(),
        db_path=str(db),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    # Both tickers failed → both logged as 'unhandled' errors, runner
    # completed with state DONE.
    assert runner.state == bf_mod.BackfillState.DONE
    assert runner.tickers_done == 2


async def _async_noop(ev, events):
    events.append(ev)


def test_extract_last_bar_ts_handles_legacy_dict_with_ts_field(tmp_path):
    """_extract_last_bar_ts handles candles where ts is dict-shape."""
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        event_sink=_sink,
    )
    yesterday = date.today() - timedelta(days=1)
    candles = [
        {"time": {"year": yesterday.year, "month": yesterday.month,
                  "day": yesterday.day}},
    ]
    out = runner._extract_last_bar_ts(candles)
    assert out == yesterday.isoformat()


def test_extract_last_bar_ts_skips_malformed_candles(tmp_path):
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        event_sink=_sink,
    )
    # No time field → skipped.
    out = runner._extract_last_bar_ts([{"foo": "bar"}, {"also": "no time"}])
    assert out is None


def test_extract_last_bar_ts_skips_when_all_candles_fail(tmp_path):
    """All candles raise AttributeError → returns None."""
    from algotrader_api.ingestion.backfill import BackfillRunner

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_BoomClient(),
        db_path=str(tmp_path / "state.db"),
        event_sink=_sink,
    )

    class _Broken:
        time = "not an object"

    out = runner._extract_last_bar_ts([_Broken()])
    assert out is None


@pytest.mark.asyncio
async def test_run_emits_done_event_with_status_ok(tmp_path):
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    class _OKClient:
        async def get_shares(self):
            return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]
        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []
        async def get_candles(self, **kw):
            return [{"ts": (date.today() - timedelta(days=1)).isoformat(),
                     "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(
        client=_OKClient(),
        db_path=str(db),
        event_sink=_sink,
    )
    events = []
    runner.event_sink = lambda ev: _async_noop(ev, events)
    await runner.run(history_years=1, incremental_threshold_days=2)
    done_events = [e for e in events if e.type == "done"]
    assert done_events
    assert done_events[0].payload["status"] == "ok"

@pytest.mark.asyncio
async def test_run_processes_figis_in_parallel(tmp_path):
    """BackfillRunner should process multiple figis concurrently.

    With concurrency, total wall time for N figis × sleep(S) should be
    much less than N × S. If backfill is purely sequential the test
    fails the timing assertion.
    """
    import time

    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)
    # Add more instruments for the parallelism to matter
    con = sqlite3.connect(str(db))
    for i in range(3, 11):  # BBG003..BBG010 = 10 figis total (SBER, GAZP + 8)
        con.execute(
            "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, ?, ?, ?, ?)",
            (f"T{i}", f"BBG{i:03d}", "share", f"T{i}", "RUB", 1),
        )
    con.commit()
    con.close()

    sleep_per_call = 0.5  # seconds

    class _SlowClient:
        async def get_shares(self):
            return [{"figi": "BBG001", "ticker": "SBER", "class": "share", "name": "Sber"}]

        async def get_bonds(self): return []
        async def get_etfs(self): return []
        async def get_futures(self): return []
        async def get_options(self): return []

        async def get_candles(self, **kw):
            await asyncio.sleep(sleep_per_call)
            return [{"ts": (date.today() - timedelta(days=1)).isoformat(),
                     "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]

        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(client=_SlowClient(), db_path=str(db), event_sink=_sink)

    start = time.time()
    await runner.run(history_years=0, incremental_threshold_days=1)
    elapsed = time.time() - start

    # With concurrency (semaphore=10), 10 figis * 0.5s should finish in well
    # under 5s (vs 5s+ sequential). Generous bound.
    assert elapsed < 4.0, (
        f"backfill took {elapsed:.1f}s for 10 figis × 0.5s each — "
        f"appears sequential. Expected <4s with concurrency."
    )

@pytest.mark.asyncio
async def test_backfill_one_empty_response_marks_metadata_as_up_to_date(tmp_path):
    """When broker returns 0 candles (e.g., delisted figi), _backfill_one
    must mark metadata.last_bar_ts to today so decide_strategy skips
    the figi on the next run instead of looping forever.
    """
    db = tmp_path / "state.db"
    _seed_migrations(db, _migrations_dir())
    _seed_instruments(db)

    from datetime import date

    class _EmptyClient:
        async def get_candles(self, **kw):
            return []
        async def aclose(self):
            pass

    async def _sink(ev):
        pass

    runner = BackfillRunner(client=_EmptyClient(), db_path=str(db), event_sink=_sink)
    today = date.today()
    await runner._backfill_one(
        figi="BBG001", ticker="SBER", from_=today, to=today,
    )

    # metadata should have last_bar_ts=today (so next run skips via decide_strategy)
    con = sqlite3.connect(str(db))
    row = con.execute(
        "SELECT last_bar_ts, last_run_status FROM instrument_metadata WHERE figi='BBG001'"
    ).fetchone()
    con.close()
    assert row is not None, "metadata should be created"
    assert row[1] == "skipped", f"status should be 'skipped', got {row[1]!r}"
    # last_bar_ts must be set to today (or close to it) so next run sees
    # days_since < incremental_threshold_days and skips
    assert row[0] is not None, (
        "last_bar_ts should NOT be None — that would cause decide_strategy "
        "to return 'full' and trigger an infinite retry loop"
    )
    last_ts = date.fromisoformat(row[0])
    assert last_ts == today, f"expected {today}, got {last_ts}"


@pytest.mark.asyncio
async def test_decide_strategy_skips_figi_with_fresh_last_bar_ts():
    """If last_bar_ts = today, decide_strategy should return 'skip'."""
    from algotrader_api.ingestion.backfill import decide_strategy
    from datetime import date

    today = date.today()
    row = {"last_bar_ts": today.isoformat()}
    strategy, from_, to = decide_strategy(
        metadata_row=row, today=today, history_years=5, incremental_threshold_days=1,
    )
    assert strategy == "skip"
    assert from_ is None
    assert to is None


@pytest.fixture
def historical_gate(tmp_path, monkeypatch):
    """Real walker/fetcher/writer/CLI/gate; only HTTP and broker are isolated.

    Removing the walker's evidence call must fail the positive test. A warm-up
    public walk writes the latest genuine positive fixture bar before the PRE
    gate: an anchor-only DB is stale, so it cannot report only 'incomplete'.
    """
    from algotrader_api.db import sqlite as sqlitedb
    from algotrader_api.ml.features import check_coverage

    today = date(2024, 12, 31)
    anchor, last = date(2014, 1, 15), date(2024, 12, 30)
    figi, ticker, board, isin = 'BBG00RU000A1', 'GAZP', 'TQBR', 'RU0007661625'

    class PinnedDate(date):
        @classmethod
        def today(cls):
            return cls.fromordinal(today.toordinal())

    for name in ('ml.features', 'ml.coverage', 'ingestion.backfill',
                 'ingestion.no_trade_evidence'):
        module = importlib.import_module('algotrader_api.' + name)
        monkeypatch.setattr(module, 'date', PinnedDate)

    db = str(tmp_path / 'gate.db')
    monkeypatch.setenv('ALGOTRADER_DATA_DIR', str(tmp_path))
    sqlitedb.run_migrations(db, str(_migrations_dir()))
    sqlitedb.close_all()
    with sqlite3.connect(db) as con:
        con.execute(
            'INSERT INTO instruments '
            '(ticker, figi, class, name, currency, lot_size, isin, source_updated_at) '
            "VALUES (?, ?, 'share', 'Gazp', 'rub', 10, ?, ?)",
            (ticker, figi, isin, anchor.isoformat()),
        )
        con.execute(
            'INSERT INTO bars (figi, ts, open, high, low, close, volume, source) '
            "VALUES (?, ?, 100, 102, 99, 101, 1000, 'tinkoff')",
            (figi, anchor.isoformat()),
        )
        holidays = {r[0] for r in con.execute('SELECT date FROM moex_holidays')}

    columns = ['TRADEDATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME',
               'NUMTRADES', 'VALUE', 'SECID', 'BOARDID']
    feeds = {year: [] for year in range(anchor.year, last.year + 1)}
    current = anchor
    while current <= last:
        if current.weekday() < 5 and current.isoformat() not in holidays:
            positive = current in (anchor, last)
            feeds[current.year].append([
                current.isoformat(),
                *([100, 102, 99, 101, 1000, 5, 100000] if positive
                  else [None, None, None, None, 0, 0, 0]), ticker, board,
            ])
        current += timedelta(days=1)
    # Put genuine bars on page one, so degraded later pages preserve them.
    for rows in feeds.values():
        rows.sort(key=lambda row: row[5] == 0)
    state = {'mode': 'warmup', 'http': [], 'fetch': []}

    def http_get(url, *, params, timeout):
        assert url == ('https://iss.moex.com/iss/history/engines/stock/markets/'
                       'shares/boards/TQBR/securities/GAZP.json')
        year = int(params['from'][:4])
        assert year in feeds and params['from'] == f'{year}-01-01'
        assert params['till'] == (last.isoformat() if year == last.year
                                  else f'{year}-12-31')
        assert timeout == 30
        start = params['start']
        key = (year, start)
        assert key not in state['http'], 'extra/repeated HTTP fetch'
        state['http'].append(key)
        mode = state['mode']
        rows = feeds[year]
        if mode == 'warmup':
            assert start == 0
            page = [row[:] for row in rows if row[5] > 0]
            total, size = len(page) + 1, 500  # deliberately uncertified
        else:
            assert start in (0, 128, 256)
            page = [row[:] for row in rows[start:start + 128]]
            total, size = len(rows), 128
            if mode == 'partial':
                assert start == 0
                total, size = len(rows) + 1, 500
            elif mode == 'error' and start == 128:
                raise ConnectionError('synthetic interrupted pagination')
            elif mode == 'malformed' and start == 128:
                page[0] = page[0][:-1]
            elif mode == 'identity_mismatch' and start == 128:
                page[0][-2] = 'SBER'
        payload = {'history': {'columns': columns, 'data': page},
                   'history.cursor': {'columns': ['INDEX', 'TOTAL', 'PAGESIZE'],
                                      'data': [[start, total, size]]}}
        return SimpleNamespace(status_code=200, json=lambda: payload)

    monkeypatch.setattr(bf_mod.requests, 'get', http_get)
    # Fail closed if any other requests path is accidentally introduced.
    monkeypatch.setattr(bf_mod.requests.sessions.Session, 'request',
                        lambda *a, **kw: pytest.fail('unexpected live HTTP'))
    real_fetch = bf_mod._fetch_year_moex_outcome

    def fetch(market, actual_board, actual_ticker, year, last_trading_day=None):
        assert (market, actual_board, actual_ticker, last_trading_day) == (
            'shares', board, ticker, last)
        rows, outcome = real_fetch(market, actual_board, actual_ticker, year,
                                   last_trading_day=last_trading_day)
        state['fetch'].append((year, outcome))
        return rows, outcome

    monkeypatch.setattr(BackfillRunner, '_fetch_year_moex_outcome', staticmethod(fetch))

    def meta(actual_ticker, yesterday, *, meta_cache, meta_lock):
        assert (actual_ticker, yesterday) == (ticker, last)
        return {'market': 'shares', 'board': board, 'listed_from': anchor.isoformat(),
                'listed_till': yesterday.isoformat(),
                'isin': 'US00206R1023' if state['mode'] == 'isin_mismatch' else isin}

    monkeypatch.setattr(BackfillRunner, '_get_meta_moex', staticmethod(meta))
    client = MagicMock()
    client.get_candles.side_effect = AssertionError('unexpected broker call')

    def walk(mode):
        state.update(mode=mode, http=[], fetch=[])
        async def sink(event):
            pass

        runner = BackfillRunner(client=client, db_path=db, event_sink=sink)
        asyncio.run(runner.backfill_from_moex(today=today))
        if mode == 'isin_mismatch':
            # Existing producer identity gate rejects metadata before any fetch.
            assert state['fetch'] == state['http'] == []
        else:
            assert sorted(year for year, _ in state['fetch']) == list(feeds)
            expected_calls = [(year, start) for year, rows in feeds.items()
                              for start in (range(0, len(rows), 128)
                                            if mode in ('complete', 'identity_mismatch')
                                            else (0, 128) if mode in ('error', 'malformed')
                                            else (0,))]
            assert sorted(state['http']) == sorted(expected_calls)
        client.get_candles.assert_not_called()

    spec = importlib.util.spec_from_file_location(
        'populate_expected_bars_gate_test',
        Path(__file__).resolve().parent.parent / 'scripts/populate_expected_bars.py',
    )
    assert spec is not None and spec.loader is not None
    peb = importlib.util.module_from_spec(spec)
    # CLI imports add API_SRC to sys.path; restore it with fixture teardown.
    monkeypatch.setattr(sys, 'path', sys.path[:])
    spec.loader.exec_module(peb)
    monkeypatch.setattr(peb, 'date', PinnedDate)

    def populate():
        monkeypatch.setattr(sys, 'argv', ['populate_expected_bars', '--db', db, '--refresh'])
        assert peb.main() == 0

    def snapshot():
        con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
        con.row_factory = sqlite3.Row
        try:
            return {
                'gate': check_coverage(con, [figi]),
                'expected': con.execute('SELECT expected_bars FROM instruments').fetchone()[0],
                'bars': [tuple(r) for r in con.execute('SELECT * FROM bars ORDER BY ts')],
                'evidence': [tuple(r) for r in con.execute(
                    'SELECT session_date, expires_at FROM moex_no_trade_evidence ORDER BY session_date')],
            }
        finally:
            con.close()

    assert len(snapshot()['bars']) == 1
    walk('warmup')
    populate()
    pre = snapshot()
    assert pre['evidence'] == []
    assert len(pre['bars']) == 2
    assert pre['gate'] == [{'figi': figi, 'max_ts': last.isoformat(),
                            'bars_count': 2, 'expected': pre['expected'], 'reason': 'incomplete'}]
    assert pre['expected'] > 0 and 2 / pre['expected'] < 0.95
    yield SimpleNamespace(walk=walk, populate=populate, snapshot=snapshot, pre=pre,
                          feeds=feeds, state=state, today=today)
    sqlitedb.close_all()


def test_walker_end_to_end_gate_improves_on_temp_db(historical_gate):
    proof = historical_gate
    proof.walk('complete')
    walked = proof.snapshot()
    assert walked['bars'] == proof.pre['bars'], 'evidence must not fabricate/change OHLC'
    assert walked['expected'] == proof.pre['expected'], 'walker must not update denominator'
    zero_dates = {row[0] for rows in proof.feeds.values() for row in rows if row[5] == 0}
    assert {outcome for _, outcome in proof.state['fetch']} == {'complete'}
    for session, expires in walked['evidence']:
        days = 7 if date.fromisoformat(session) >= proof.today - timedelta(days=14) else 365
        assert expires == (proof.today + timedelta(days=days)).isoformat()
    proof.populate()
    post = proof.snapshot()
    assert post['bars'] == proof.pre['bars']
    assert post['gate'] == []
    assert post['expected'] == proof.pre['expected'] - len(zero_dates) == 2
    assert {row[0] for row in walked['evidence']} == zero_dates
    assert len(post['bars']) / post['expected'] >= 0.95
    print(f"GATE_PROOF pre=2/{proof.pre['expected']} incomplete post=2/2 "
          f"evidence={len(zero_dates)} HTTP={len(proof.state['http'])} gate=[]")


@pytest.mark.parametrize('mode', ['partial', 'error', 'malformed',
                                  'identity_mismatch', 'isin_mismatch'])
def test_walker_degraded_feed_preserves_bars_and_denominator(historical_gate, mode):
    proof = historical_gate
    proof.walk(mode)
    walked = proof.snapshot()
    assert walked['bars'] == proof.pre['bars']
    assert walked['evidence'] == []
    proof.populate()
    assert proof.snapshot() == proof.pre
    expected_outcomes = set() if mode == 'isin_mismatch' else {mode}
    assert {outcome for _, outcome in proof.state['fetch']} == expected_outcomes
    print(f"NEGATIVE_PROOF mode={mode} bars=2 evidence=0 "
          f"expected={proof.pre['expected']} HTTP={len(proof.state['http'])}")
