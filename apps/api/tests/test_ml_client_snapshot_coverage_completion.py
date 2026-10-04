"""Offline behavioral regressions for consumption gates and read-side caches."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from algotrader_api.db import bars_sqlite, sqlite as sqlitedb
from algotrader_api.db.secrets import set_secret
from algotrader_api.ingestion import client as client_factory
from algotrader_api.ingestion import rate_limit
from algotrader_api.ml import features
from algotrader_api import ui_snapshot


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 9, 22)


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    # Never let a constructor or cache fall back to operational paths or .env.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALGOTRADER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ALGOTRADER_SQLITE_PATH", str(tmp_path / "data/state.db"))
    monkeypatch.setenv("ALGOTRADER_UI_SNAPSHOT_PATH", str(tmp_path / "snapshot.json"))
    monkeypatch.setenv("ALGOTRADER_INGEST_FAKE", "1")
    monkeypatch.delenv("ALGOTRADER_TINKOFF_TARGET", raising=False)
    monkeypatch.setattr(ui_snapshot, "_version", 0)
    monkeypatch.setattr(ui_snapshot, "_last_refresh_t", 0.0)
    monkeypatch.setattr(features, "date", FixedDate)


def seed_instrument(conn, figi="F", instrument_class="share", expected=1, listed_till=None):
    conn.execute(
        "INSERT INTO instruments (figi, ticker, class, expected_bars, listed_till, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, 'Synthetic instrument', 'RUB', 1)",
        (figi, figi, instrument_class, expected, listed_till),
    )
    conn.execute("INSERT INTO instrument_metadata (figi) VALUES (?)", (figi,))
    conn.commit()


def seed_bar(conn, ts="2026-09-21", figi="F"):
    conn.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES (?, ?, 10, 11, 9, 10, 1, 'synth')", (figi, ts),
    )
    conn.commit()


def test_ml_gate_keeps_cached_denominator_and_future_delisting(fresh_db):
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn, expected=2, listed_till="2026-12-31")
    seed_bar(conn)
    conn.execute(
        "INSERT INTO moex_no_trade_evidence (figi, session_date, board, isin, expires_at) "
        "VALUES ('F', '2026-09-18', 'TQBR', 'SYNTHETIC', '2026-10-01')"
    )
    conn.commit()
    result = features.check_coverage(conn, ["F"])
    assert result == [{"figi": "F", "max_ts": "2026-09-21", "bars_count": 1,
                       "expected": 2, "reason": "incomplete"}]
    assert conn.execute("SELECT expected_bars FROM instruments").fetchone()[0] == 2
    conn.execute("UPDATE instruments SET expected_bars=1")
    conn.commit()
    assert features.build_features(conn, ["F"])["rows"] == [
        {"figi": "F", "max_ts": "2026-09-21", "bars_count": 1}]


def test_ml_evidence_loop_stops_before_non_session_delisting(fresh_db):
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn, listed_till="2026-09-20")
    seed_bar(conn)
    # Sunday effective end is already older than MAX(ts), so no stale loop.
    assert features.check_coverage(conn, ["F"]) == []
    conn.execute("UPDATE bars SET ts='2026-09-19'")
    conn.commit()
    assert features.check_coverage(conn, ["F"])[0]["reason"] == "stale"


def test_ml_bond_recovery_logs_backfill_failure_and_rechecks(fresh_db, caplog):
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn, instrument_class="bond")
    # Real backfill cannot resolve an injected file connection when SQLite denies PRAGMA.
    conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_PRAGMA and name == "database_list"
                        else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(features.InsufficientDataError) as caught:
            features.build_features(conn, ["F"])
        assert caught.value.attempted_recovery is True
        assert caught.value.failing_figis[0]["reason"] == "both"
        assert "auto_recovery_backfill_failed" in caplog.text
        assert "insufficient_data_error" in caplog.text
        assert not conn.in_transaction
    finally:
        conn.set_authorizer(None)


def test_ml_bond_recovery_builds_matrix_from_actually_committed_candles(fresh_db, monkeypatch):
    from algotrader_api.ingestion import backfill, fake_client
    original_client = fake_client.InMemoryTinkoffClient
    class SeededClient(original_client):
        def __init__(self):
            super().__init__()
            self.set_candles("F", [{"ts": "2026-09-21", "open": 10, "high": 11,
                                    "low": 9, "close": 10, "volume": 1}])
    monkeypatch.setattr(fake_client, "InMemoryTinkoffClient", SeededClient)
    monkeypatch.setattr(backfill, "date", FixedDate)
    monkeypatch.setattr(rate_limit, "_GLOBAL", rate_limit.RateLimiter())
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn, instrument_class="bond")
    matrix = features.build_features(conn, ["F"])
    assert matrix == {"figis": ["F"], "rows": [
        {"figi": "F", "max_ts": "2026-09-21", "bars_count": 1}]}
    assert conn.execute("SELECT total_bars FROM instrument_metadata WHERE figi='F'").fetchone()[0] == 1
    assert features.check_coverage(conn, ["F"]) == []
    assert not conn.in_transaction


def test_ml_recovery_sql_error_preserves_original_failures(fresh_db, caplog):
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn)
    conn.set_authorizer(lambda action, table, column, *_: sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_READ and table == "instruments" and column == "class"
                        else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(features.InsufficientDataError) as caught:
            features.build_features(conn, ["F"])
        assert isinstance(caught.value.__cause__, sqlite3.DatabaseError)
        assert caught.value.failing_figis[0]["figi"] == "F"
        assert caught.value.attempted_recovery is True
        assert "auto_recovery_raised" in caplog.text
    finally:
        conn.set_authorizer(None)


def test_ml_view_helpers_filter_nontradeable_and_observe_new_rows(fresh_db):
    conn = sqlitedb.get_connection(fresh_db)
    assert features.row_count(fresh_db) == 0
    assert features.date_range(fresh_db) == (None, None)
    seed_instrument(conn)
    seed_instrument(conn, "NON", instrument_class="future")
    seed_bar(conn, "2026-09-01")
    seed_bar(conn, "2026-08-01", "NON")
    assert features.row_count(fresh_db) == 1
    assert features.date_range(fresh_db) == ("2026-09-01", "2026-09-01")
    seed_bar(conn, "2026-09-21")
    assert features.row_count(fresh_db) == 2
    assert features.date_range(fresh_db) == ("2026-09-01", "2026-09-21")


@pytest.fixture
def offline_sdk(monkeypatch):
    # Constructor only imports SDK metadata; no channel or network is created.
    sdk = ModuleType("t_tech.invest")
    constants = ModuleType("t_tech.invest.constants")
    constants.INVEST_GRPC_API = "offline-production"
    constants.INVEST_GRPC_API_SANDBOX = "offline-sandbox"
    monkeypatch.setitem(sys.modules, "t_tech.invest", sdk)
    monkeypatch.setitem(sys.modules, "t_tech.invest.constants", constants)


@pytest.mark.parametrize("blob,target", [
    ({"broker": {"environment": "production"}}, "production"),
    ({"broker": {"environment": "sandbox"}}, "sandbox"),
    ({"broker": {"environment": "invalid"}}, "sandbox"),
    ({}, "sandbox"),
    ([], "sandbox"),
    ("malformed", "sandbox"),
])
def test_factory_reads_real_settings_and_synthetic_token(fresh_db, offline_sdk, blob, target):
    conn = sqlitedb.get_connection(fresh_db)
    set_secret(fresh_db, "broker_token", "synthetic-token-only")
    value = "{broken" if blob == "malformed" else json.dumps(blob)
    conn.execute("INSERT INTO settings (key, value, version) VALUES ('main', ?, 'v1')", (value,))
    conn.commit()
    result = client_factory.make_client(use_fake=False, sqlite_path=fresh_db)
    assert isinstance(result, client_factory.TinkoffClient)
    assert result._target == "offline-" + target
    assert result._token == "synthetic-token-only"
    assert result._client is None
    assert result._services is None
    asyncio.run(result.aclose())


def test_factory_observes_settings_and_token_changes_without_restart(fresh_db, offline_sdk):
    conn = sqlitedb.get_connection(fresh_db)
    set_secret(fresh_db, "broker_token", "synthetic-first")
    conn.execute("INSERT INTO settings (key,value,version) VALUES ('main', ?, 'v1')",
                 (json.dumps({"broker": {"environment": "production"}}),))
    conn.commit()
    first = client_factory.make_client(use_fake=False, sqlite_path=fresh_db)
    conn.execute("UPDATE settings SET value=?", (json.dumps({"broker": {"environment": "sandbox"}}),))
    conn.commit()
    set_secret(fresh_db, "broker_token", "synthetic-second")
    second = client_factory.make_client(use_fake=False, sqlite_path=fresh_db)
    assert (first._target, first._token) == ("offline-production", "synthetic-first")
    assert (second._target, second._token) == ("offline-sandbox", "synthetic-second")


def test_factory_without_saved_settings_uses_safe_default(fresh_db, offline_sdk):
    set_secret(fresh_db, "broker_token", "synthetic-no-settings")
    result = client_factory.make_client(use_fake=False, sqlite_path=fresh_db)
    assert result._target == "offline-sandbox"


def test_snapshot_buckets_external_health_scores_and_limits_worst(fresh_db, monkeypatch):
    from algotrader_api.data_quality import health
    # Substitute the report dependency, not the snapshot aggregation under test.
    # Current health penalties do not emit 90..99, but HealthReport's contract permits it.
    original_report = health.HealthReport
    scores = {"F0": 100, "F1": 95, "F2": 70, "F3": 40, "F4": 30, "F5": 20, "F6": 10}
    class ExternalHealthReport(original_report):
        def __init__(self, **kwargs):
            kwargs["health_score"] = scores[kwargs["figi"]]
            super().__init__(**kwargs)
    monkeypatch.setattr(health, "HealthReport", ExternalHealthReport)
    conn = sqlitedb.get_connection(fresh_db)
    for figi in scores:
        seed_instrument(conn, figi)
    result = ui_snapshot.compute_snapshot(fresh_db)
    assert result.pending_counts["by_health"] == {"100": 1, "99-90": 1, "89-50": 1, "<50": 4}
    assert [entry["health_score"] for entry in result.pending_counts["worst"]] == [10, 20, 30, 40, 70]
    assert result.health == {figi: {"health_score": score} for figi, score in scores.items()}


@pytest.mark.parametrize("payload", ["not-json", '{"generated_at": "bad"}',
                                    '{"generated_at": 0, "version": 8}'])
def test_snapshot_rebuilds_corrupt_or_expired_cache(fresh_db, monkeypatch, payload):
    target = ui_snapshot.get_snapshot_path(fresh_db)
    target.write_text(payload)
    monkeypatch.setattr(ui_snapshot, "time", SimpleNamespace(time=lambda: 1000, monotonic=lambda: 50))
    assert ui_snapshot.maybe_refresh(fresh_db) is True
    raw = json.loads(target.read_text())
    assert raw["generated_at"] == 1000
    assert raw["version"] == 1
    assert raw["pending_counts"]["total"] == 0
    assert list(target.parent.glob(".snapshot.json.*.tmp")) == []
    assert ui_snapshot.maybe_refresh(fresh_db) is False
    assert json.loads(target.read_text()) == raw


def test_snapshot_path_override_is_read_fresh(fresh_db, tmp_path, monkeypatch):
    first = ui_snapshot.get_snapshot_path(fresh_db)
    monkeypatch.setenv("ALGOTRADER_UI_SNAPSHOT_PATH", str(tmp_path / "next/cache.json"))
    second = ui_snapshot.get_snapshot_path(fresh_db)
    assert second != first
    assert ui_snapshot.maybe_refresh(fresh_db, force=True)
    assert second.exists() and not first.exists()
    monkeypatch.delenv("ALGOTRADER_UI_SNAPSHOT_PATH")
    assert ui_snapshot.get_snapshot_path(fresh_db) == Path(fresh_db).parent / "ui_snapshot.json"


def test_snapshot_nonexistent_db_pending_counts_does_not_create_file(tmp_path):
    missing = tmp_path / "absent.db"
    result = ui_snapshot._build_pending_counts(str(missing), 2)
    assert result == {"new": 0, "stale": 0, "up_to_date": 0, "error": 0, "total": 0,
                      "by_health": {"100": 0, "99-90": 0, "89-50": 0, "<50": 0}, "worst": []}
    assert not missing.exists()


def test_pending_counts_classifies_exact_staleness_boundary(fresh_db, monkeypatch):
    monkeypatch.setattr(ui_snapshot, "date", FixedDate)
    conn = sqlitedb.get_connection(fresh_db)
    for figi, last_ts, status in [("FRESH", "2026-09-20", "ok"),
                                  ("STALE", "2026-09-19", "ok"),
                                  ("ERROR", "2026-09-21", "error"),
                                  ("NEW", None, None)]:
        seed_instrument(conn, figi)
        conn.execute("UPDATE instrument_metadata SET last_bar_ts=?, last_run_status=? WHERE figi=?",
                     (last_ts, status, figi))
    conn.commit()
    counts = ui_snapshot._build_pending_counts(fresh_db, 2)
    assert {key: counts[key] for key in ("new", "stale", "up_to_date", "error", "total")} == {
        "new": 1, "stale": 1, "up_to_date": 1, "error": 1, "total": 4}


def test_snapshot_degrades_on_invalid_bar_and_metadata_dates(fresh_db):
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn)
    seed_bar(conn, "not-a-date")
    conn.execute("UPDATE instrument_metadata SET last_bar_ts='invalid', last_run_status='ok'")
    conn.commit()
    snap = ui_snapshot.compute_snapshot(fresh_db)
    assert snap.tickers[0]["bars"] == 1
    assert snap.tickers[0]["gaps"] == 0
    assert snap.pending_counts["new"] == 1
    assert snap.pending_counts["total"] == 1
    assert snap.pending_counts["worst"] == []
    assert snap.health == {}


def test_snapshot_fsync_failure_still_publishes_complete_cache(fresh_db, monkeypatch):
    def unsupported_fsync(_):
        raise OSError("synthetic filesystem has no fsync")
    monkeypatch.setattr(ui_snapshot.os, "fsync", unsupported_fsync)
    assert ui_snapshot.maybe_refresh(fresh_db, force=True)
    raw = json.loads(ui_snapshot.get_snapshot_path(fresh_db).read_text())
    assert ui_snapshot.UiSnapshot.from_dict(raw).to_dict() == raw


def test_throttle_expiry_and_broken_callback_do_not_poison_other_buckets(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(rate_limit, "time", SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1000))
    def broken_callback(method, until):
        raise RuntimeError("synthetic subscriber failure")
    limiter = rate_limit.RateLimiter(on_throttle=broken_callback)
    limiter.signal_throttle("candles")
    assert limiter.tokens("candles") == 10
    assert limiter.tokens("shares") == 14
    assert limiter.cap_for("candles") == 10
    now[0] = 400.0
    assert limiter.tokens("candles") == 14
    assert limiter.cap_for("candles") == 14
    assert limiter.cap_for("candles") == 14
    assert limiter._bucket_for("candles").throttled_until == 0
    asyncio.run(limiter.acquire("shares"))
    limiter.record_success("shares")
    limiter.record_error("shares")
    assert limiter.error_rate("shares") == 0.5


def test_global_limiter_lazily_creates_one_shared_instance(monkeypatch):
    monkeypatch.setattr(rate_limit, "_GLOBAL", None)
    first = rate_limit.get_global()
    assert isinstance(first, rate_limit.RateLimiter)
    assert rate_limit.get_global() is first


def test_global_limiter_rechecks_after_another_initializer_wins(monkeypatch):
    winner = rate_limit.RateLimiter(rate=7)
    class RacingLock:
        def __enter__(self):
            rate_limit._GLOBAL = winner
        def __exit__(self, *_):
            return False
    monkeypatch.setattr(rate_limit, "_GLOBAL", None)
    monkeypatch.setattr(rate_limit, "_GLOBAL_LOCK", RacingLock())
    assert rate_limit.get_global() is winner
    assert rate_limit.get_global().rate == 7


@pytest.mark.parametrize("candles", [[], [{"ts": "2026-09-21", "open": None,
                                         "high": 11, "low": 9, "close": 10}]])
def test_rowcount_empty_or_missing_ohlc_is_noop(fresh_db, candles):
    assert bars_sqlite.replace_bars_for_figi_with_rowcount(fresh_db, "F", candles) == 0
    assert bars_sqlite.count_bars(fresh_db) == 0


def test_rowcount_rejects_foreign_object_before_mutation(fresh_db):
    candle = SimpleNamespace(figi="OTHER", ts="2026-09-21")
    with pytest.raises(ValueError, match="foreign-FIGI fail-closed"):
        bars_sqlite.replace_bars_for_figi_with_rowcount(fresh_db, "F", [candle])
    assert bars_sqlite.count_bars(fresh_db) == 0


def test_empty_transaction_helper_leaves_borrowed_connection_idle(fresh_db):
    conn = sqlitedb.get_connection(fresh_db)
    assert bars_sqlite._replace_bars_for_figi_tx(conn, "F", [], replace=True, source="synth") == 0
    assert not conn.in_transaction


@pytest.mark.parametrize("writer", [bars_sqlite.replace_bars_for_figi,
                                    bars_sqlite.replace_bars_for_figi_with_rowcount])
@pytest.mark.parametrize("deny_rollback", [False, True])
def test_bar_failure_preserves_rows_and_releases_lock(fresh_db, writer, deny_rollback):
    from algotrader_api.ingestion.writer_lock import writer_lock
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn)
    seed_bar(conn, "2026-09-01")
    conn.execute("CREATE TRIGGER reject_bar BEFORE INSERT ON bars BEGIN SELECT RAISE(ABORT, 'synthetic insert rejection'); END")
    conn.commit()
    if deny_rollback:
        conn.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
                            if action == sqlite3.SQLITE_TRANSACTION and name == "ROLLBACK"
                            else sqlite3.SQLITE_OK)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic insert rejection"):
            writer(fresh_db, "F", [{"ts": "2026-09-21", "open": 10, "high": 11,
                                    "low": 9, "close": 10}], source="synth")
        with writer_lock(fresh_db, role="bar-writer", phase="bars", timeout_seconds=0):
            pass
    finally:
        conn.set_authorizer(None)
        conn.rollback()
    assert bars_sqlite.list_bars(fresh_db, "F")[0]["ts"] == "2026-09-01"
    assert bars_sqlite.count_bars(fresh_db) == 1
    assert conn.execute("SELECT total_bars FROM instrument_metadata WHERE figi='F'").fetchone()[0] == 0


@pytest.mark.parametrize("writer", [bars_sqlite.replace_bars_for_figi,
                                    bars_sqlite.replace_bars_for_figi_with_rowcount])
def test_snapshot_publish_failure_never_rolls_back_committed_bars(fresh_db, tmp_path, monkeypatch, writer):
    obstacle = tmp_path / "not-a-directory"
    obstacle.write_text("synthetic path obstruction")
    monkeypatch.setenv("ALGOTRADER_UI_SNAPSHOT_PATH", str(obstacle / "snapshot.json"))
    conn = sqlitedb.get_connection(fresh_db)
    seed_instrument(conn)
    assert writer(fresh_db, "F", [{"ts": "2026-09-21", "open": 10, "high": 11,
                                  "low": 9, "close": 10}], source="synth") == 1
    assert bars_sqlite.count_bars(fresh_db) == 1
    assert conn.execute("SELECT total_bars FROM instrument_metadata WHERE figi='F'").fetchone()[0] == 1
    assert not conn.in_transaction
