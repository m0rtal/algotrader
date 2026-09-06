# algotrader observability stack

Self-hosted OpenTelemetry stack for the algotrader-api backend.

## What it runs

- **otel-collector** (port 4317 gRPC, 4318 HTTP) — receives OTLP from the API, fans out to Loki and Tempo.
- **loki** (port 3100) — log aggregation.
- **tempo** (port 3200) — distributed tracing storage.
- **grafana** (port 3000) — UI, pre-configured to correlate traces (Tempo) and logs (Loki) via `correlation_id`.

## Usage

```bash
cd ops/otel-collector
docker compose up -d
```

Then point the backend at the collector:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
uv run uvicorn algotrader_api.main:app --host 127.0.0.1 --port 8000
```

## Drill-down flow

1. Open Grafana → **Explore** → pick **Loki** → query `{service_name="algotrader-api"}`.
2. Click any log line with a `trace_id` field → Grafana pivots to the matching Tempo trace.
3. In Tempo, expand the waterfall → see HTTP root span + child `db.query.*` spans + manual `settings.put` spans.
4. Filter logs by `correlation_id="abc-123"` to follow a single request end-to-end.

## Links

- Grafana UI: <http://localhost:3000>
- Collector health: <http://localhost:13133>
- Tempo HTTP API: <http://localhost:3200>

## Production notes

- **LAN-bind by default**: ports 4317/4318 are exposed only on the docker bridge.
- **No auth on Grafana** by default — set `GF_AUTH_ANONYMOUS_ENABLED=false` and configure users before exposing.
- **Loki/Tempo data is on the docker volume** — back up `loki-data` / `tempo-data` for retention.
