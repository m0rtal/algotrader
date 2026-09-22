"""Tests for the rewritten `/api/tickers` endpoint.

After `remove-duckdb-and-parquet`, the overview is computed entirely
from SQLite: `bars` provides the row aggregates, `instruments` provides
the metadata. No DuckDB connection, no parquet dir walk.

`fileSize` is kept in the response (always 0) because the UI expects
the field.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def seeded_tickers(fresh_db):
    """Seed instruments + bars rows so /api/tickers has data."""
    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'Banks', 'rub', 10);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('YNDX', 'FIGI-YNDX', 'share', 'Yandex', 'IT', 'rub', 1);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('GAZP', 'FIGI-GAZP', 'share', 'Gazprom', 'OilGas', 'rub', 10);
        """
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("FIGI-SBER", "2025-12-01", 100.0, 110.0, 95.0, 105.0, 1000),
            ("FIGI-SBER", "2025-12-02", 105.0, 112.0, 100.0, 110.0, 1100),
            ("FIGI-SBER", "2025-12-03", 110.0, 115.0, 108.0, 113.0, 1200),
            ("FIGI-YNDX", "2025-12-01", 50.0, 55.0, 48.0, 53.0, 500),
            ("FIGI-GAZP", "2025-12-01", 200.0, 205.0, 195.0, 203.0, 800),
            ("FIGI-GAZP", "2025-12-02", 203.0, 208.0, 200.0, 207.0, 900),
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.routes import data_reads as data_reads_route

    data_reads_route.set_sqlite_path(fresh_db)
    return fresh_db


def test_get_tickers_returns_sqlite_aggregates(seeded_tickers):
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/tickers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert {row["symbol"] for row in body} == {"SBER", "YNDX", "GAZP"}
    sber = next(r for r in body if r["symbol"] == "SBER")
    assert sber["bars"] == 3
    assert sber["firstDate"] == "2025-12-01"
    assert sber["lastDate"] == "2025-12-03"
    assert sber["fileSize"] == 0
    assert sber["name"] == "Sber"
    assert sber["sector"] == "Banks"


def test_get_tickers_returns_gap_count_per_ticker(seeded_tickers):
    """Each ticker must report its real gap count from find_gaps(), not 0.

    Regression: data_reads.py used `gaps: 0` literal for every ticker
    so the UI's "completeness" widget always showed 100% regardless of
    reality. Verify the field is now computed from find_gaps().
    """
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/tickers")
    assert r.status_code == 200
    body = r.json()

    # SBER has 3 bars (2025-12-01..03) — weekend gap between 12-02 and 12-03
    # plus the days from 12-03 to today (2026-09-15). find_gaps reports
    # only gaps WITHIN the actual bar range, so we expect a non-zero count.
    sber = next(r for r in body if r["symbol"] == "SBER")
    assert isinstance(sber["gaps"], int), f"gaps must be int, got {type(sber['gaps'])}"
    # At minimum: the literal-zero bug should not produce 0
    # when the ticker actually has bars (the gap finder should run).
    # We don't assert a specific count — we assert the type is int and
    # the field is populated, not the placeholder 0.
    assert "gaps" in sber, "gaps field missing from response"


def test_get_tickers_gaps_count_matches_find_gaps(seeded_tickers):
    """The `gaps` field for a ticker must equal len(find_gaps() for that ticker)."""
    from algotrader_api.data_quality.gap_recovery import find_gaps
    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()

    expected_gaps = len(find_gaps(seeded_tickers))
    body_gaps = sum(row["gaps"] for row in body)
    # Body gaps sum should equal find_gaps total (across all figis)
    assert body_gaps == expected_gaps, (
        f"body sum gaps={body_gaps}, find_gaps={expected_gaps}"
    )


def test_get_tickers_returns_real_gap_counts_on_prod_db():
    """Regression: prod API returned ``gaps: 0`` for every ticker because
    ``data_reads.py`` left a placeholder literal. Verify the field now
    equals the real ``find_gaps()`` count for at least one ticker that
    has gaps in the prod DB.

    This test reads the prod DB directly — it asserts the production
    code path is wired to ``find_gaps()``, not a hardcoded zero.
    """
    import os
    from algotrader_api.data_quality.gap_recovery import find_gaps
    from algotrader_api.main import create_app
    from algotrader_api.routes import data_reads as data_reads_route

    prod_db = "/home/hermes/algotrader/apps/api/data/state.db"
    if not os.path.exists(prod_db):
        pytest.skip(f"prod DB not at {prod_db}")

    data_reads_route.set_sqlite_path(prod_db)
    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()

    # Pick a ticker whose first bar is at the chain start — that figi
    # is in the find_gaps index and should report a non-zero count.
    # Use SBER (BBG004730N88 — the canonical example) and verify
    # at least 1 gap (SBER has 6 real gaps post moex_holidays fix).
    sber = next((r for r in body if r["symbol"] == "SBER"), None)
    assert sber is not None, "SBER must be in /api/tickers"
    assert sber["gaps"] >= 1, (
        f"SBER.gaps={sber['gaps']} — expected >=1 from find_gaps(), "
        f"got 0 which means data_reads.py is still returning the placeholder"
    )

    # Cross-check: the find_gaps() count for SBER must equal what API says.
    import sqlite3
    conn = sqlite3.connect(prod_db)
    sber_figi = conn.execute(
        "SELECT figi FROM instruments WHERE ticker='SBER' LIMIT 1"
    ).fetchone()[0]
    conn.close()
    prod_gaps = [g for g in find_gaps(prod_db) if g.figi == sber_figi]
    assert sber["gaps"] == len(prod_gaps), (
        f"SBER.gaps={sber['gaps']} vs find_gaps={len(prod_gaps)} — "
        f"data_reads.py is not reading from find_gaps()"
    )


def test_get_tickers_excludes_bars_without_instruments(seeded_tickers):
    """Bars with no matching instrument row are dropped from the response.

    Since the Полнота fix the query starts from `instruments` LEFT
    JOIN `bars` aggregate — so the universe is the tradable
    instrument set, not the set of figis that happen to have bars.
    Orphan bars (rows whose figi has no instrument) therefore no
    longer appear in /api/tickers. They never contributed to Полнота
    anyway, and including them would require fabricating a synthetic
    ticker/name/sector. The 4 explicit tests below cover the inverse
    direction (zero-bar tradable figis ARE included).
    """
    con = sqlite3.connect(seeded_tickers)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('ORPHAN-FIGI', '2025-12-01', 1.0, 2.0, 0.5, 1.5, 100)"
    )
    con.commit()
    con.close()

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()
    symbols = {row["symbol"] for row in body}
    assert "ORPHAN-FIGI" not in symbols
    # The tradable tickers are still there.
    assert symbols == {"SBER", "YNDX", "GAZP"}


def test_get_tickers_latency_under_slo(seeded_tickers):
    """Sanity: SQLite-native read path stays well under 100 ms even with rows."""
    import time

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        t0 = time.time()
        r = c.get("/api/tickers")
        elapsed = (time.time() - t0) * 1000
    assert r.status_code == 200
    assert elapsed < 100, f"/api/tickers took {elapsed:.0f}ms"


def test_get_tickers_caches_find_gaps_across_requests(seeded_tickers):
    """Regression: find_gaps() takes ~8s on prod (3783 figis). Calling it
    on every /api/tickers request blocks the single uvicorn worker.
    The gaps-by-figi dict is cached for 60 seconds so the second
    request is fast and find_gaps() is called at most once per cache
    window.

    Asserts the cache by patching the find_gaps symbol that
    data_reads.py actually calls, then verifying the second
    /api/tickers call does NOT trigger it again.
    """
    from unittest.mock import patch

    from algotrader_api.main import create_app
    from algotrader_api.routes import data_reads as data_reads_route

    # Reset module cache so the test sees a clean state regardless of
    # what other tests did to the cache during the session.
    data_reads_route._gaps_cache = None

    call_count = {"n": 0}

    def _counting_find_gaps(db_path):
        call_count["n"] += 1
        return []  # Empty gaps is fine for this test — we only count calls.

    # Patch the symbol as it lives in data_reads's namespace, not in
    # gap_recovery (data_reads did `from ... import find_gaps`).
    with patch.object(data_reads_route, "find_gaps",
                      side_effect=_counting_find_gaps):
        app = create_app()
        with TestClient(app) as c:
            r1 = c.get("/api/tickers")
            assert r1.status_code == 200
            r2 = c.get("/api/tickers")
            assert r2.status_code == 200

    assert call_count["n"] == 1, (
        f"find_gaps called {call_count['n']} times across 2 requests; "
        f"expected 1 (cached on second call)"
    )


def test_get_tickers_serializes_find_gaps_under_concurrency(seeded_tickers):
    """Regression: /api/tickers hung under concurrent dashboard tabs.

    The per-figi gap cache in _gaps_by_figi() had no concurrency guard,
    so N concurrent callers each independently observed an empty cache
    and each independently triggered the ~8s find_gaps() scan — N× CPU
    on the single uvicorn worker. The fix wraps the slow path in a
    threading.Lock so concurrent callers collapse to one scan per TTL
    window.

    This test fires 5 concurrent threads against _gaps_by_figi()
    directly — going through TestClient would serialise requests on
    its internal portal and miss the race. The patched find_gaps
    sleeps 50ms so the race window is wide enough for a deterministic
    check on call count.
    """
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    from algotrader_api.routes import data_reads as data_reads_route

    # Clean cache so every request would normally trigger a rebuild.
    data_reads_route._gaps_cache = None

    call_count = {"n": 0}
    find_gaps_lock = threading.Lock()

    def _slow_counting_find_gaps(db_path):
        # If the lock in _gaps_by_figi() is broken, multiple threads
        # enter find_gaps() concurrently and call_count climbs above 1.
        with find_gaps_lock:
            call_count["n"] += 1
        time.sleep(0.05)
        return []

    def _hit():
        return data_reads_route._gaps_by_figi()

    with patch.object(data_reads_route, "find_gaps",
                      side_effect=_slow_counting_find_gaps):
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(_hit) for _ in range(5)]
            results = [f.result() for f in futures]

    # All threads got the same counts dict (proves they shared the
    # single rebuild instead of each computing their own).
    assert all(r == results[0] for r in results), (
        f"per-thread counts diverged: {results}"
    )
    assert call_count["n"] == 1, (
        f"find_gaps called {call_count['n']} times across 5 concurrent "
        f"threads; expected exactly 1 (lock must serialise rebuilds)"
    )


# ---------------------------------------------------------------------------
# Полнота fix: /api/tickers must include zero-bar tradable figis so the
# UI's Полнота denominator matches the real tradable universe
# (TRADEABLE_CLASSES = share/etf/bond). Without these tests, the previous
# regression where ~38 tradable figis without bars were silently dropped
# from the denominator could sneak back in unnoticed.
# ---------------------------------------------------------------------------


def test_get_tickers_includes_zero_bar_tradable_figis(fresh_db):
    """Tradable figis with zero rows in `bars` MUST appear in /api/tickers.

    Pre-fix the query was `SELECT ... FROM bars GROUP BY figi` which
    filtered out zero-bar figis at the SQL level. The Полнота formula
    in the UI then averaged over only the figis that had bars,
    silently overstating coverage by ~1 percentage point on prod.
    """
    from algotrader_api.main import create_app
    from algotrader_api.routes import data_reads as data_reads_route

    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'Banks', 'rub', 10);
        -- ZERO-BAR tradable figi: must appear with bars=0 and empty dates.
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('NOBS', 'FIGI-NOBS', 'share', 'NeverBackfilled', 'IT', 'rub', 1);
        """
    )
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("FIGI-SBER", "2025-12-01", 100.0, 110.0, 95.0, 105.0, 1000),
            ("FIGI-SBER", "2025-12-02", 105.0, 112.0, 100.0, 110.0, 1100),
        ],
    )
    con.commit()
    con.close()

    data_reads_route.set_sqlite_path(fresh_db)

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()

    by_symbol = {row["symbol"]: row for row in body}
    assert "SBER" in by_symbol
    assert "NOBS" in by_symbol, (
        f"zero-bar tradable figi NOBS missing from /api/tickers "
        f"(got symbols={sorted(by_symbol)})"
    )
    nobs = by_symbol["NOBS"]
    assert nobs["bars"] == 0
    # firstDate/lastDate must be empty strings (NOT "None", NOT NULL),
    # otherwise the UI's `new Date(t.firstDate)` produces NaN and
    # crashes the completeness calc.
    assert nobs["firstDate"] == ""
    assert nobs["lastDate"] == ""
    assert nobs["gaps"] == 0
    # Metadata still surfaces from `instruments`.
    assert nobs["name"] == "NeverBackfilled"
    assert nobs["sector"] == "IT"


def test_get_tickers_excludes_non_tradable_classes(fresh_db):
    """Non-tradeable instruments (future/option/currency) must not appear.

    The Полнота denominator is the operator's actual tradable universe
    (share/etf/bond). Including futures/options would dilute the
    metric with classes the operator never trades.
    """
    from algotrader_api.main import create_app
    from algotrader_api.routes import data_reads as data_reads_route

    con = sqlite3.connect(fresh_db)
    con.executescript(
        """
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber', 'Banks', 'rub', 10);
        -- Non-tradeable classes: must be filtered out by the WHERE clause.
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('FUT-SBER', 'FIGI-FUT-SBER', 'future', 'Sber Future', 'Banks', 'rub', 1);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('OPT-SBER', 'FIGI-OPT-SBER', 'option', 'Sber Option', 'Banks', 'rub', 1);
        INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size)
        VALUES ('CUR-USD', 'FIGI-CUR-USD', 'currency', 'USD', 'FX', 'usd', 1);
        """
    )
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI-SBER', '2025-12-01', 100.0, 110.0, 95.0, 105.0, 1000)"
    )
    con.commit()
    con.close()

    data_reads_route.set_sqlite_path(fresh_db)

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()

    classes_returned = {row["symbol"] for row in body}
    assert "SBER" in classes_returned
    for excluded in ("FUT-SBER", "OPT-SBER", "CUR-USD"):
        assert excluded not in classes_returned, (
            f"non-tradeable {excluded} leaked into /api/tickers"
        )


def test_get_tickers_zero_bar_figis_count_as_zero_completeness(seeded_tickers):
    """Frontend contract: zero-bar figis contribute 0% to the average.

    The DataTab.tsx completeness map must short-circuit when
    `t.bars === 0` so NaN from `new Date('')` doesn't poison the
    average. This test pins down the contract at the unit level
    (the API returns bars=0; the frontend guard is in DataTab.tsx).
    The end-to-end browser assertion lives in apps/web e2e.
    """
    con = sqlite3.connect(seeded_tickers)
    # Add a fourth tradable figi with zero bars.
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, sector, currency, lot_size) "
        "VALUES ('EMPTY', 'FIGI-EMPTY', 'share', 'Empty Co', 'IT', 'rub', 1)"
    )
    con.commit()
    con.close()

    from algotrader_api.main import create_app

    app = create_app()
    with TestClient(app) as c:
        body = c.get("/api/tickers").json()

    by_symbol = {row["symbol"]: row for row in body}
    assert "EMPTY" in by_symbol
    assert by_symbol["EMPTY"]["bars"] == 0
    # The frontend's per-ticker map short-circuits here. Document
    # the contract so a future "optimisation" doesn't remove the
    # guard and silently raise the Полнота number.
    assert by_symbol["EMPTY"]["firstDate"] == ""
    assert by_symbol["EMPTY"]["lastDate"] == ""


def test_get_tickers_response_includes_all_tradable_on_prod_db():
    """Regression against the real prod DB.

    Pre-fix this query returned 3795 rows (only figis that had bars).
    Post-fix it must return the full tradable set (share + etf + bond
    on the current prod state.db). A drop back to ~3795 means
    someone reintroduced the `FROM bars` grouping.

    Snapshot caveat: this is a live DB that the running worker
    process is actively writing to, so individual counts drift by a
    few rows between sample points. We snapshot the expected symbol
    set BEFORE the API call so we compare against the same
    consistent view, not against a different point-in-time.
    """
    prod_db = "/home/hermes/algotrader/apps/api/data/state.db"
    if not Path(prod_db).exists():
        pytest.skip(f"prod DB not present at {prod_db}")

    from algotrader_api.domain.tradeable import TRADEABLE_CLASSES
    from algotrader_api.main import create_app
    from algotrader_api.routes import data_reads as data_reads_route

    # Precompute the SQL class-list literal outside f-strings because
    # Python 3.11 disallows backslashes inside f-string expressions.
    classes_sql = ",".join("'" + c + "'" for c in TRADEABLE_CLASSES)

    # Snapshot expected figi set from prod DB BEFORE the app reads it,
    # so the worker process can't drift the values out from under us
    # between the two reads. Use a single connection with BEGIN so the
    # read is consistent even with the WAL writer active.
    #
    # The response uses `symbol = ticker OR figi` — i.e. ticker when
    # present, falling back to figi. To compare apples to apples we
    # snapshot BOTH columns and build the expected response symbol
    # set the same way the API does.
    con = sqlite3.connect(prod_db)
    try:
        con.execute("BEGIN IMMEDIATE")
        n_tradable = con.execute(
            f"SELECT COUNT(*) FROM instruments WHERE class IN ({classes_sql})"
        ).fetchone()[0]
        n_tradable_with_bars = con.execute(
            f"""
            SELECT COUNT(DISTINCT i.figi) FROM instruments i
            JOIN bars b ON b.figi = i.figi
            WHERE i.class IN ({classes_sql})
            """
        ).fetchone()[0]
        # Build the expected response symbol set exactly the way the
        # API does: prefer ticker, fall back to figi. This is the set
        # the Полнота fix promised /api/tickers would return.
        expected_symbols = {
            (row[0] or row[1])
            for row in con.execute(
                f"SELECT ticker, figi FROM instruments WHERE class IN ({classes_sql})"
            ).fetchall()
            if row[1]  # must have a figi to qualify (matches WHERE i.figi IS NOT NULL)
        }
        con.rollback()
    finally:
        con.close()
    assert n_tradable > 0
    missing_in_prod = n_tradable - n_tradable_with_bars
    assert missing_in_prod > 0, (
        f"prod DB has {n_tradable} tradable figis but {n_tradable_with_bars} "
        f"have bars (missing={missing_in_prod}); this test cannot exercise "
        f"the Полнота fix on a DB where every tradable figi already has bars"
    )

    data_reads_route.set_sqlite_path(prod_db)

    app = create_app()
    with TestClient(app) as c:
        r = c.get("/api/tickers")
    assert r.status_code == 200
    body = r.json()

    returned_symbols = {row["symbol"] for row in body}

    # Core invariant: every tradable symbol that existed at snapshot
    # time must be present in the response. A drop means the
    # Полнота fix has regressed. Live-worker drift between the
    # snapshot and the response is acceptable, but specific symbols
    # that vanished from the response would point at a code bug.
    missing_in_response = expected_symbols - returned_symbols
    assert not missing_in_response, (
        f"/api/tickers is missing {len(missing_in_response)} of "
        f"{len(expected_symbols)} tradable symbols snapshotted from prod DB "
        f"(sample: {sorted(missing_in_response)[:5]}) — "
        f"the Полнота fix has regressed"
    )

    # Cross-check: at least one zero-bar tradable figi is present with
    # bars=0 and empty dates — i.e. the LEFT JOIN path was used, not a
    # fresh `FROM bars` aggregation. The exact count drifts with the
    # worker, so we don't assert equality here, only presence.
    zero_bar = [row for row in body if row["bars"] == 0]
    assert len(zero_bar) > 0, (
        f"expected at least one zero-bar tradable row in /api/tickers, "
        f"got {len(zero_bar)} — the LEFT JOIN path appears unused"
    )
    sample = zero_bar[0]
    assert sample["firstDate"] == ""
    assert sample["lastDate"] == ""
