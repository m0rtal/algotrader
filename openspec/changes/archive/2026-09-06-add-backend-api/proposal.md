# add-backend-api — proposal

## Why

Frontend v1 (commit `4cdae4c`) ходит за данными через MSW моки. Это удобно для разработки UI, но mock data — статичные. Чтобы перейти к реальным данным, нужен бэкенд, который отдаёт тот же контракт `/api/*` но из реального storage с синтетическими или pre-fetched данными.

Сейчас (2026-09-06) токен Tinkoff ещё не получен — sandbox аккаунт создаётся. Live ingestion (`add-data-fetch` change) появится позже. Этот change — только read API поверх готового storage.

## What Changes

- **Добавить `apps/api/`** — Python 3.11+ FastAPI сервис
- **Storage layer:**
  - Parquet файлы — daily OHLCV bars per ticker (`data/bars/<ticker>.parquet`, zstd compressed)
  - DuckDB — read-only analytical query layer поверх parquet
  - SQLite — settings + metadata + signal/portfolio state
- **API contract:** строго совпадает с текущим MSW mock (16 эндпойнтов включая Settings)
- **Seed data:** минимальный synth dataset (16 тикеров, 252 бара каждый, deterministic seed) — чтобы можно было поднять сервис до того как реальные данные скачаны
- **Запуск:** `uvicorn apps.api.main:app --host 0.0.0.0 --port 8000` (dev), `--host 127.0.0.1` (prod — LAN-only)
- **Тесты:** pytest, ≥95% coverage (hard rule для money-path проекта)
- **Frontend change:** `VITE_API_BASE_URL` переключается с `/api` (MSW) на `http://localhost:8000/api` (реальный бэкенд); MSW остаётся как dev fallback

## Impact

**Affected:**
- `apps/web/src/lib/api.ts` — без изменений (тот же fetch contract)
- `apps/web/.env.example` — добавить `VITE_API_BASE_URL=http://localhost:8000/api`
- `apps/web/src/mocks/` — НЕ удаляем, остаются как dev fallback
- New: `apps/api/` (~15 файлов)
- New: `data/` (storage dir, gitignored кроме `.gitkeep`)
- `pyproject.toml` — root level, uv-managed
- `README.md` — quickstart раздел

**Не затрагивает:**
- `packages/shared/src/index.ts` — Zod schemas остаются shared (api + web читают тот же файл)
- Frontend components/tabs/pages — нулевые изменения

**Подготовка к следующим changes:**
- `add-data-fetch` (следующий) — Tinkoff SDK fetcher в `apps/api/ingestion/`, cron или systemd timer
- `add-ml-pipeline` — `apps/api/ml/` (regime detection, model train, predict)
- `add-live-trading` — `apps/api/broker/` (Tinkoff SDK, position management)

## Non-Goals

- **Live data ingestion** — отдельный change `add-data-fetch`, не здесь
- **ML/strategy training** — отдельный change
- **Auto-trading / order placement** — отдельный change, после ≥6 мес paper trading
- **Auth** — отдельный change `add-auth`
- **Multi-user support** — self-hosted, один пользователь
- **Tier-1 broker abstractions** (alpaca, IB) — тинькофф only
- **WebSocket streaming** — REST polling пока достаточно (фронт уже polls через TanStack Query staleTime 30s)
- **Backtest engine** — отдельный change `add-backtest-engine`
- **Full APM/metrics (Prometheus, Datadog, NewRelic)** — только traces+logs, metrics отдельным change если нужны

## Observability

Все события, errors, и state changes идут через OpenTelemetry и экспортируются
по OTLP в collector (Loki для логов, Tempo/Jaeger для traces). Каждый request
получает `correlation_id` (UUIDv7, monotonic) который propagate'ится через:
- HTTP request → response header `X-Correlation-ID`
- Async tasks (contextvars)
- DB queries (instrumented через DuckDB/SQLite tracers)
- Outgoing gRPC calls (Tinkoff SDK в `add-data-fetch` change)

**Что логируется:**
- HTTP request: method, path, status, latency_ms, correlation_id, client_ip, user_agent
- DB query: type, table/collection, duration_ms, row_count (для каждого query в DuckDB и SQLite)
- Settings mutations: event name, version hash, before/after diff (без token values)
- Errors: stacktrace + context attributes (request_id, user input keys, never values)
- Lifecycle events: service start/stop, seed runs, migrations applied

**Что НЕ логируется:**
- Tinkoff tokens (даже `tokenLast4` strip'ается на log boundary)
- Account IDs в URL (только в structured attributes с маской)
- HTTP body content для Settings PUT (только top-level keys)
- Health check noise (sample 1/10, не каждый)

**Storage:**
- Primary: OTel collector → Loki (logs) + Tempo (traces) на LAN
- Fallback: stdout JSON если collector недоступен (degraded mode, логируется warning)
- No local file rotation (Ponytail: если collector упал — пусть пайплайн тоже падает, проще заметить)

**Env vars:**
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317  # collector gRPC
OTEL_SERVICE_NAME=algotrader-api
OTEL_RESOURCE_ATTRIBUTES=service.version=0.1.0,deployment.environment=dev
ALGOTRADER_LOG_LEVEL=INFO
ALGOTRADER_LOG_SAMPLE_HEALTH=0.1  # 10% health check logs
```

## Risks

| Risk | Mitigation |
|---|---|
| API contract drift с frontend | Backend тесты читают `packages/shared` schemas; CI прогоняет и web, и api на одних типах |
| SQLite concurrent writes | Backend single-process, WAL mode, retry-on-busy |
| DuckDB lock при parquet file replacement | Use ATTACH/DETACH pattern, не заменять файл in-place |
| Slow first query (cold DuckDB) | Warm-up endpoint `/health` открывает connections при старте |
| Python dependency hell | uv lockfile, pinned versions, reproducible builds |
| Tinkoff SDK upstream archived (`Tinkoff/invest-python` GitHub) | Использовать GitLab mirror `opensource.tbank.ru/invest/invest-python` (active, v0.2.0-beta); pin version в lockfile |
