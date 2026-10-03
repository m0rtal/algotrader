"""Offline route contracts against SQLite/files and the real OTel SDK."""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def module(name):
    # The routes package also exports endpoint functions; import actual modules.
    return importlib.import_module(f"algotrader_api.{name}")


@pytest.fixture
def isolated_routes(fresh_db, tmp_path, monkeypatch):
    reads = module("routes.data_reads")
    settings = module("routes.settings")
    backfill = module("routes.backfill")
    snapshot = module("ui_snapshot")
    monkeypatch.setattr(reads, "_sqlite_path_holder", {"path": fresh_db})
    monkeypatch.setattr(reads, "_gaps_cache", None)
    monkeypatch.setattr(settings, "_sqlite_path_holder", {"path": fresh_db})
    monkeypatch.setattr(backfill, "_slot", backfill._RunnerSlot())
    monkeypatch.setattr(snapshot, "_last_refresh_t", 0.0)
    monkeypatch.setattr(snapshot, "_version", 0)
    path = tmp_path / "ui_snapshot.json"
    monkeypatch.setenv("ALGOTRADER_UI_SNAPSHOT_PATH", str(path))
    app = FastAPI()
    for name in ("data_reads", "backfill", "admin"):
        app.include_router(module(f"routes.{name}").router)
    # No main lifespan: migrations and data_dir come from fresh_db, never prod.
    with TestClient(app) as client:
        yield client, fresh_db, path


@pytest.mark.parametrize("route,key", [
    ("/api/tickers", "tickers"),
    ("/api/admin/backfill/pending", "pending_counts"),
])
@pytest.mark.parametrize("state", [
    "missing", "corrupt", "wrong_shape", "bad_timestamp", "fresh", "stale", "pytest",
])
def test_snapshot_read_routes_cache_and_recovery(isolated_routes, monkeypatch, route, key, state):
    client, db, path = isolated_routes
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO instruments (figi, ticker, class, name, currency, lot_size) VALUES ('F', 'TEST', 'share', 'Test', 'rub', 1)")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    cached = [{"symbol": "CACHED"}] if key == "tickers" else {"total": 999}
    payload = {"generated_at": time.time(), key: cached}
    if state == "corrupt":
        path.write_text("{broken", encoding="utf-8")
    elif state != "missing":
        if state == "wrong_shape":
            payload[key] = None
        elif state == "bad_timestamp":
            payload["generated_at"] = "not-a-number"
        elif state == "stale":
            payload["generated_at"] = 0
        elif state == "pytest":
            monkeypatch.setenv("PYTEST_CURRENT_TEST", "offline snapshot request")
        path.write_text(json.dumps(payload), encoding="utf-8")
    response = client.get(route)
    assert response.status_code == 200
    if state == "fresh":
        assert response.json() == cached
        assert json.loads(path.read_text())[key] == cached
    elif key == "tickers":
        assert [row["symbol"] for row in response.json()] == ["TEST"]
        assert response.json()[0]["bars"] == 0
    else:
        assert response.json()["total"] == 1
        assert response.json()["new"] == 1
        assert json.loads(path.read_text())[key] == response.json()


@pytest.mark.parametrize("state", ["missing", "fresh", "stale", "pytest"])
def test_ui_snapshot_file_response_and_refresh(isolated_routes, monkeypatch, state):
    client, db, path = isolated_routes
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    if state != "missing":
        path.write_text(json.dumps({"generated_at": time.time() if state == "fresh" else 0,
                                    "tickers": [{"symbol": "CACHED"}]}))
    if state == "pytest":
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "offline file response")
    response = client.get("/api/ui-snapshot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=5"
    assert response.headers["content-type"] == "application/json"
    assert response.json() == json.loads(path.read_text())
    assert response.json()["tickers"] == ([{"symbol": "CACHED"}] if state == "fresh" else [])


def test_unconfigured_read_path_fails_explicitly(monkeypatch):
    reads = module("routes.data_reads")
    monkeypatch.setattr(reads, "_sqlite_path_holder", {})
    with pytest.raises(RuntimeError, match="sqlite path not configured"):
        reads.get_logs()


def test_logs_preserve_short_and_sqlite_timestamps(isolated_routes):
    client, db, _ = isolated_routes
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO ingestion_logs (ts, run_id, level, figi, message) VALUES (?, 1, ?, ?, ?)",
                        [("9999", "debug", None, "short"),
                         ("9999-01-01 12:34:56", "warn", "F", "w" * 100)])
    response = client.get("/api/logs")
    assert response.status_code == 200
    assert response.json() == [
        {"ts": "9999-01-01 12:34:56", "tone": "warn", "text": "F " + "w" * 90},
        {"ts": "9999", "tone": "flat", "text": "short"},
    ]


def test_expired_gap_cache_does_not_leak_old_database(isolated_routes, monkeypatch):
    reads = module("routes.data_reads")
    monkeypatch.setattr(reads, "_gaps_cache", (float("-inf"), {"old-database": 7}))
    assert reads._gaps_by_figi() == {}
    assert reads._gaps_by_figi() == {}


@pytest.mark.parametrize("history", [False, True])
def test_sse_replays_history_drains_live_and_unsubscribes(isolated_routes, history):
    backfill = module("routes.backfill")
    from algotrader_api.ingestion.backfill import BackfillEvent

    async def exercise():
        replay = BackfillEvent(type="progress", run_id=7, ts="replay", payload={"bars": 2})
        live = BackfillEvent(type="progress", run_id=7, ts="live", payload={"bars": 3})
        done = BackfillEvent(type="done", run_id=7, ts="done", payload={"bars": 3})
        if history:
            backfill._slot.publish(replay)
        response = await backfill.backfill_events()
        assert response.media_type == "text/event-stream"
        assert response.headers["x-accel-buffering"] == "no"
        assert len(backfill._slot.subscribers) == 1
        async def consume():
            return [chunk async for chunk in response.body_iterator]

        consumer = asyncio.create_task(consume())
        # Start replay before publishing, so live events are not also history.
        await asyncio.sleep(0)
        await backfill._event_sink(live)
        await backfill._event_sink(done)
        chunks = await asyncio.wait_for(consumer, timeout=2)
        expected = [replay, live, done] if history else [live, done]
        assert len(chunks) == len(expected)
        for chunk, event in zip(chunks, expected):
            event_line, data_line, *_ = chunk.split("\n")
            assert event_line == f"event: {event.type}"
            assert json.loads(data_line.removeprefix("data: ")) == {
                "type": event.type, "run_id": 7, "ts": event.ts, "payload": event.payload,
            }
        assert not backfill._slot.subscribers

    asyncio.run(exercise())


@pytest.mark.parametrize("finished", ["invalid-date", "2999-01-01T00:00:00+00:00"])
def test_backfill_status_bad_or_timezone_aware_cycle(isolated_routes, finished):
    client, db, _ = isolated_routes
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO pipeline_runs (started_at, finished_at, rc) VALUES (?, ?, 0)",
                    (finished, finished))
    response = client.get("/api/admin/backfill/status")
    assert response.status_code == 200
    assert response.json()["last_cycle_age_seconds"] == (None if finished == "invalid-date" else 0)


def test_cycle_age_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / "absent.db"
    assert module("routes.backfill")._last_cycle_age_seconds(str(path)) is None
    assert not path.exists()


def test_stale_breakdown_invalid_rows_and_pre_retry_schema(isolated_routes):
    client, db, _ = isolated_routes
    # Endpoint creates pipeline_log for a fresh database.
    assert client.get("/api/admin/data-stale-breakdown").status_code == 200
    with sqlite3.connect(db) as con:
        con.execute("INSERT INTO instruments (figi, ticker, class, name, currency, lot_size) VALUES ('BAD', 'BAD', 'share', 'Bad', 'rub', 1)")
        con.execute("INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES ('BAD', 'invalid-date', 1, 1, 1, 1, 1)")
        con.execute("INSERT INTO pipeline_log (phase, started_at, finished_at, result) VALUES ('dividends', 'bad', 'bad', 'ok')")
        con.execute("DROP TABLE dividends_throttle_pending")
    response = client.get("/api/admin/data-stale-breakdown")
    assert response.status_code == 200
    body = response.json()
    assert body["bars"]["no_bars_ever"] == 1
    assert body["bars"]["samples_no_bars"] == [["BAD", "BAD", "share"]]
    assert body["pipeline_age_hours"] == {"corporate_actions": None, "dividends": None}
    assert body["dividends_pending_retry"] == 0


def test_ml_readiness_counts_only_tradeable_rows_and_adjustments(isolated_routes):
    client, db, _ = isolated_routes
    empty = client.get("/api/admin/ml-readiness")
    assert empty.status_code == 200
    assert empty.json() == {"rows": 0, "min_ts": None, "max_ts": None, "forward_adjusted_rows": 0}
    with sqlite3.connect(db) as con:
        con.executemany("INSERT INTO instruments (figi, ticker, class, name, currency, lot_size) VALUES (?, ?, ?, 'Test', 'rub', 1)",
                        [("S", "S", "share"), ("F", "F", "future")])
        con.executemany("INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, 1, 1)",
                        [("S", "2026-01-01"), ("S", "2026-01-02"), ("F", "2025-01-01")])
        con.execute("INSERT INTO bars_adjusted (figi, ts, adj_open, adj_high, adj_low, adj_close, adj_volume, computed_at) VALUES ('S', '2026-01-01', 1, 1, 1, 1, 1, '2026-01-02')")
    response = client.get("/api/admin/ml-readiness")
    assert response.status_code == 200
    assert response.json() == {"rows": 2, "min_ts": "2026-01-01", "max_ts": "2026-01-02", "forward_adjusted_rows": 1}


@pytest.mark.parametrize("attributes", [None, {"deployment.environment": "offline-test"}])
def test_tracing_exports_real_spans_and_is_idempotent(monkeypatch, attributes):
    tracing = module("observability.tracing")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    from opentelemetry.util._once import Once

    class MemoryExporter(SpanExporter):
        def __init__(self, *, endpoint, insecure):
            self.endpoint = endpoint
            self.insecure = insecure
            self.spans = []
            self.closed = False

        def export(self, spans):
            self.spans.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            self.closed = True

    # Replace only the network exporter. Provider, resource and processor are real.
    monkeypatch.setattr(tracing, "OTLPSpanExporter", MemoryExporter)
    monkeypatch.setattr(tracing, "_initialized", False)
    monkeypatch.setattr(tracing, "_exporter", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())
    provider = None
    try:
        tracing.setup_tracing(service_name="offline-api", otlp_endpoint="memory://test",
                              resource_attributes=attributes)
        provider = trace.get_tracer_provider()
        processor = tracing._exporter
        exporter = processor.span_exporter
        tracing.setup_tracing(service_name="ignored", otlp_endpoint="memory://ignored")
        assert trace.get_tracer_provider() is provider
        assert tracing._exporter is processor
        with tracing.get_tracer("coverage_completion").start_as_current_span("request") as span:
            span.set_attribute("http.response.status_code", 200)
        assert provider.force_flush(timeout_millis=1000)
        assert len(exporter.spans) == 1
        recorded = exporter.spans[0]
        assert recorded.name == "request"
        assert recorded.attributes["http.response.status_code"] == 200
        assert recorded.resource.attributes["service.name"] == "offline-api"
        if attributes:
            assert recorded.resource.attributes["deployment.environment"] == "offline-test"
        assert exporter.endpoint == "memory://test"
        assert exporter.insecure is True
        tracing.shutdown_tracing()
        assert exporter.closed
        assert tracing._initialized is False
    finally:
        if provider is not None:
            provider.shutdown()


def test_tracing_shutdown_before_setup_uses_proxy_provider(monkeypatch):
    tracing = module("observability.tracing")
    from opentelemetry import trace
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(tracing, "_initialized", True)
    tracing.shutdown_tracing()
    assert tracing._initialized is False
    assert isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)
