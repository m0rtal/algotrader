# add-backend-api — design

## Stack

- **Python 3.11+** — async via asyncio
- **uv** — package manager + Python version pinning
- **FastAPI 0.115+** — framework
- **Uvicorn** — ASGI server (dev + prod single-process)
- **DuckDB 1.1+** — analytical engine для parquet + ad-hoc queries
- **SQLite 3.45+** (stdlib `sqlite3`) — settings + metadata
- **Pydantic v2** — request/response models, validation
- **OpenTelemetry SDK** — `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-fastapi`, `opentelemetry-instrumentation-sqlite3`, `opentelemetry-instrumentation-asyncpg` (для будущего postgres)
- **structlog** — structured JSON logs (event logger поверх stdlib logging → OTel log exporter)
- **Pytest 8+** — tests
- **Coverage** — coverage.py + pytest-cov

**Не используем:**
- ORM (SQLAlchemy) — прямые SQL запросы, проще
- Celery/RQ — пока нет async jobs, fetch on-demand
- Redis — single-process, нет shared state вне SQLite WAL
- PostgreSQL — overkill для одного юзера, локальный self-hosted

## File Layout

```
apps/api/
├── pyproject.toml             # uv-managed
├── README.md                  # how to run, env vars
├── src/algotrader_api/
│   ├── __init__.py
│   ├── main.py                # FastAPI app factory + lifespan
│   ├── config.py              # env vars, paths
│   ├── deps.py                # FastAPI dependencies (db connections)
│   ├── settings.py            # pydantic-settings
│   ├── observability/
│   │   ├── __init__.py
│   │   ├── tracing.py         # OTel tracer provider + OTLP exporter setup
│   │   ├── logging.py         # structlog → OTel log exporter + stdout fallback
│   │   ├── correlation.py     # contextvar-based correlation_id, FastAPI middleware
│   │   ├── scrubbers.py       # PII/token redaction structlog processors
│   │   ├── middleware.py      # latency + correlation_id middleware
│   │   └── instrumentation.py # manual spans wrappers для DB queries
│   ├── db/
│   │   ├── __init__.py
│   │   ├── duck.py            # DuckDB connection + OTel-instrumented query helpers
│   │   ├── sqlite.py          # SQLite connection + migrations + OTel spans
│   │   └── migrations/
│   │       └── 001_init.sql
│   ├── seed/
│   │   ├── __init__.py
│   │   └── synth.py           # deterministic seed data generator
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── kpis.py
│   │   ├── regime.py
│   │   ├── tickers.py
│   │   ├── signals.py
│   │   ├── trades.py
│   │   ├── portfolio.py
│   │   ├── folds.py
│   │   ├── bars.py
│   │   ├── model.py
│   │   ├── pipeline.py
│   │   ├── logs.py
│   │   └── settings.py        # GET/PUT/DELETE /api/settings
│   └── schemas/
│       ├── __init__.py        # re-export from @algotrader/shared
│       └── api.py             # request/response wrappers
└── tests/
    ├── conftest.py
    ├── test_kpis.py
    ├── test_bars.py
    ├── test_settings.py
    ├── test_observability.py  # correlation_id propagation, scrubber, span attributes
    ├── test_logging.py        # structlog → OTel log mapping, fallback
    └── ...

data/
├── bars/                      # parquet files (gitignored)
├── state.db                   # SQLite (gitignored)
└── .gitkeep
```

## Observability Architecture

```
┌─────────────────────┐
│  FastAPI request    │
│  (correlation_id    │
│   middleware)       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐    ┌────────────────────┐
│  Route handler      │───▶│  structlog event   │
│  (FastAPI auto-     │    │  (logger.info      │
│   instrumented via  │    │   "settings.put")  │
│   OTel FastAPI      │    └─────────┬──────────┘
│   instrumentation)  │              │
└──────────┬──────────┘              │
           │                         │
           ▼                         ▼
┌─────────────────────┐    ┌────────────────────┐
│  DuckDB / SQLite    │    │  OTel LogExporter  │
│  (manual spans:     │    │  (structlog →      │
│   db.query,         │    │   OTel logs)       │
│   duration_ms,      │    └─────────┬──────────┘
│   row_count)        │              │
└──────────┬──────────┘              │
           │                         │
           ▼                         ▼
┌──────────────────────────────────────────┐
│  OTel Collector (OTLP gRPC :4317)        │
│  ├── Tempo/Jaeger (traces)               │
│  ├── Loki (logs)                         │
│  └── Prometheus (optional, future)       │
└──────────────────────────────────────────┘
```

### Correlation ID

Каждый request получает UUIDv7 (monotonic, sortable) при входе:
1. Middleware читает `X-Correlation-ID` header если есть, иначе генерит
2. Кладёт в `contextvars.ContextVar` чтобы был доступен везде (log events, DB spans)
3. Возвращает в response header `X-Correlation-ID` для клиентской трассировки
4. Background tasks получают через `contextvars.copy_context()`

### Logging (structlog → OTel)

Двухуровневая система:

**structlog** для domain events:
```python
logger.info("settings.put",
    version=old_version,
    new_version=new_version,
    section_keys=list(patch.keys()),
    correlation_id=correlation_id.get(),
)
# → {"event": "settings.put", "level": "info", "timestamp": "...", ...}
```

**OTel log exporter** собирает structlog events и шлёт в collector как OTel `LogRecord`s.

**Fallback:** если collector недоступен, exporter переключается в degraded mode — события идут в stdout JSON, single warning event при первом failure.

**Scrubbers** в structlog processor pipeline:
- `redact_token_fields` — поля с именем `token`, `password`, `secret` → `"[REDACTED]"`
- `mask_account_id` — `accountId: ABC-12345` → `accountId: AB***45`
- `drop_health_noisy` — health checks, sampled `1 - ALGOTRADER_LOG_SAMPLE_HEALTH` отбрасываются
- `truncate_long_strings` — values > 1KB truncate до 512 chars + `"...truncated"`

### Tracing

**Auto-instrumentation:**
- FastAPI middleware: каждый route автоматически span с `http.method`, `http.route`, `http.status_code`, latency
- SQLite: каждый query через `db.sqlite.execute()` обёрнут в span `db.query.sqlite` с `db.statement`, `db.duration_ms`, `db.row_count`
- DuckDB: то же самое через `db.duck.query()`

**Manual spans** для критичных секций:
- Settings PUT: span `settings.put` с attributes `settings.version_old`, `settings.version_new`, `settings.section`
- Seed run: span `seed.run` с `seed.ticker_count`, `seed.bar_count`, `seed.duration_ms`
- Lifespan: span `service.lifespan` с `service.event` (start/stop)

**Span attributes convention:** snake_case `domain.attribute` (e.g., `settings.version_new`).

### Trace ↔ Log Correlation

В каждом log event автоматически добавляется:
- `trace_id` и `span_id` из active OTel context (для click-through из Loki в Tempo)
- `correlation_id` из contextvar

Позволяет по конкретному error в логах → прыгнуть в trace UI → увидеть полный waterfall (HTTP request → DB queries → response).

### Health Sampling

Health checks (GET /health) высокочастотные (load balancer probes). Логировать каждый — spam. Sampling:
- По умолчанию 10% (`ALGOTRADER_LOG_SAMPLE_HEALTH=0.1`)
- Traces — тоже sampled
- Failure responses (5xx, 4xx кроме 401/403/404) — **всегда** логируются

### Test Strategy

```python
# tests/test_observability.py
def test_correlation_id_propagates_from_header():
    response = client.get("/api/kpis", headers={"X-Correlation-ID": "test-123"})
    assert response.headers["X-Correlation-ID"] == "test-123"

def test_correlation_id_generated_when_missing():
    response = client.get("/api/kpis")
    assert "X-Correlation-ID" in response.headers

def test_token_field_redacted_in_logs(caplog):
    client.put("/api/settings", json={"values": {"broker": {"token": "secret"}}})
    assert "secret" not in str(caplog.text)
    assert "[REDACTED]" in str(caplog.text)

def test_db_query_span_attributes():
    with trace.get_tracer().start_as_current_span("test") as span:
        db.execute("SELECT 1")
    # Verify span.attributes has db.statement, db.row_count
```

## Data Flow

```
HTTP request
    ↓
FastAPI route handler (apps/api/src/algotrader_api/routes/*.py)
    ↓
Pydantic schema validation (@algotrader/shared types)
    ↓
DB query (DuckDB for bars, SQLite for settings/state)
    ↓
Pydantic response model
    ↓
JSON
    ↓
Frontend (apps/web/src/lib/api.ts — fetch wrapper, без изменений)
    ↓
React Query cache (TanStack Query)
```

## Storage Schema

### Parquet bars (`data/bars/<ticker>.parquet`)

| Column | Type | Notes |
|---|---|---|
| `ts` | DATE | trading date |
| `open` | DECIMAL(18,4) | |
| `high` | DECIMAL(18,4) | |
| `low` | DECIMAL(18,4) | |
| `close` | DECIMAL(18,4) | |
| `volume` | BIGINT | shares traded |
| `adj_close` | DECIMAL(18,4) | split-adjusted |

Zstd compression, ~2KB per ticker per year. 252 bars × 16 tickers × 5 лет = ~80KB total.

DuckDB schema: same columns + `ticker VARCHAR` denormalized via filename или UNION ALL across files (Ponytail: простой `read_parquet('data/bars/*.parquet', hive_partitioning=false)`).

### SQLite state (`data/state.db`)

```sql
CREATE TABLE settings (
  key VARCHAR PRIMARY KEY,
  value JSON NOT NULL,
  version VARCHAR NOT NULL,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker VARCHAR NOT NULL,
  signal_date DATE NOT NULL,
  score REAL NOT NULL,
  rank INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(ticker, signal_date)
);

CREATE TABLE trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker VARCHAR NOT NULL,
  side VARCHAR NOT NULL,        -- 'buy' / 'sell'
  qty INTEGER NOT NULL,
  price DECIMAL(18,4) NOT NULL,
  executed_at TIMESTAMP NOT NULL,
  pnl DECIMAL(18,4),
  is_paper BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE portfolio (
  as_of_date DATE PRIMARY KEY,
  equity DECIMAL(18,4) NOT NULL,
  cash DECIMAL(18,4) NOT NULL,
  positions JSON NOT NULL,      -- {ticker: qty}
  drawdown_pct REAL NOT NULL
);

CREATE TABLE logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  level VARCHAR NOT NULL,        -- 'info' / 'warn' / 'error'
  source VARCHAR NOT NULL,
  message TEXT NOT NULL
);
```

## API Contract

**Strict parity с frontend MSW handlers.** Тот же path, те же response shape. Полный список в `packages/shared/src/index.ts` (Zod schemas) и `apps/web/src/mocks/handlers.ts` (reference implementation).

| Endpoint | Method | Source |
|---|---|---|
| `/api/kpis` | GET | Synth: equity, drawdown, sharpe (calculated from bars) |
| `/api/regime` | GET | Synth: trend/range/volatile + IMOEX index level |
| `/api/tickers` | GET | DuckDB: list of tickers with metadata |
| `/api/signals` | GET | SQLite: latest signals joined with ticker metadata |
| `/api/trades` | GET | SQLite: latest N trades |
| `/api/portfolio` | GET | SQLite: latest portfolio snapshot |
| `/api/folds` | GET | Synth: 5 walk-forward folds |
| `/api/bars/{symbol}` | GET | DuckDB: bars for ticker |
| `/api/model` | GET | Synth: model metadata |
| `/api/model-features` | GET | Synth: feature importance |
| `/api/pipeline` | GET | Synth: pipeline steps with status |
| `/api/logs` | GET | SQLite: last N logs |
| `/api/settings` | GET | SQLite: settings row |
| `/api/settings` | PUT | SQLite: settings row + version check (409 on mismatch) |
| `/api/settings` | DELETE | SQLite: drop settings row → defaults |
| `/api/health` | GET | Liveness — DB connectivity check |

## Lifespan / Startup

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Init SQLite (run migrations if needed)
    db.init_sqlite(SQLITE_PATH)

    # Ensure data dirs exist
    ensure_data_dirs()

    # Warm DuckDB connection (pre-load catalog)
    with db.duck.connect() as conn:
        conn.execute("SELECT 1").fetchone()

    # Seed if no bars exist (dev convenience)
    if not list_bars_files():
        seed.run_synth_seed()

    yield

    # Cleanup
    db.sqlite.close()
```

## Configuration

Env vars (`apps/api/.env`, pydantic-settings):

```
ALGOTRADER_API_HOST=0.0.0.0
ALGOTRADER_API_PORT=8000
ALGOTRADER_DATA_DIR=./data
ALGOTRADER_LOG_LEVEL=INFO
ALGOTRADER_LOG_FORMAT=json
ALGOTRADER_LOG_SAMPLE_HEALTH=0.1

# OpenTelemetry — OTLP gRPC
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_SERVICE_NAME=algotrader-api
OTEL_RESOURCE_ATTRIBUTES=service.version=0.1.0,deployment.environment=dev

# CORS
ALGOTRADER_CORS_ORIGINS=http://localhost:5173,http://192.168.1.101:5173
```

`.env.example` shipped, real `.env` gitignored.

## Error Handling

- 400 — Zod validation fail (FastAPI auto via Pydantic)
- 404 — ticker not found
- 409 — settings version mismatch
- 500 — anything else, logged with full stacktrace + OTel error span + correlation_id
- Все errors — JSON `{"error": "<code>", "message": "<human>", "details": ..., "correlation_id": "..."}`
- Каждый error response логируется через structlog + создаёт OTel error event span

## Security (MVP)

- **No auth** (deferred)
- **LAN-bind by default** (`ALGOTRADER_API_HOST=127.0.0.1` в production)
- **CORS whitelist** для known frontend origins
- **No secrets в URLs** — token в PUT body или future bearer header
- **Input validation** — Pydantic strict mode на всех endpoints
- **Logging** — structlog с scrubbers, OTel экспорт только через OTLP (не в файлы), collector access controlled LAN-only
- **Structured scrubbers в pipeline** — token, password, secret fields заменяются до того как event покинет процесс

## Tinkoff SDK (для будущего `add-data-fetch`)

**НЕ использовать:** `Tinkoff/invest-python` на GitHub — помечен **"Public archive"** (заархивирован, maintainer переехал).

**Активный source:** GitLab mirror Т-Банка — `opensource.tbank.ru/invest/invest-python`. Latest version `0.2.0-beta111` (активная разработка, T-Invest API 1.49, Июнь 2026). Mirror публикует в PyPI как `tinkoff-investments`.

**Альтернативы:**
- `tinkoff-invest` (PyPI) — community-maintained, 2026 update, без привязки к T-Bank GitLab
- REST через прямой gRPC кодген из `protos/tinkoff/invest/grpc` (overkill для MVP)

**Решение для `add-data-fetch` change:** `pip install tinkoff-investments>=0.2.0,<0.3.0` pin, ingest в отдельный worker. SDK version pin в `apps/api/pyproject.toml` lockfile чтобы не уехала при очередной beta-выпуске.

## Testing Strategy

- **pytest** + pytest-asyncio
- **Coverage ≥95%** по всем 4 метрикам (hard rule)
- **TestClient** (FastAPI) для HTTP layer
- **In-memory SQLite** (`:memory:`) для state DB в тестах
- **Tmpdir** для parquet files
- **Deterministic seed** (`seed=42`) — все assertions на exact values

Test files (~30 файлов, по одному на каждый route + integration):

```
tests/
├── conftest.py           # app, client, db fixtures
├── test_health.py
├── test_kpis.py
├── test_regime.py
├── test_tickers.py
├── test_bars.py          # DuckDB parquet roundtrip
├── test_signals.py
├── test_trades.py
├── test_portfolio.py
├── test_folds.py
├── test_model.py
├── test_pipeline.py
├── test_logs.py
├── test_settings_get.py
├── test_settings_put.py
├── test_settings_delete.py
├── test_seed.py
└── test_db.py
```

## Rollout

1. `apps/api/` skeleton + health endpoint
2. SQLite migrations + settings GET/PUT/DELETE
3. Synth seed → DuckDB query → bars endpoint
4. Remaining read endpoints (KPIs, regime, tickers, signals, trades, portfolio, folds, model, model-features, pipeline, logs)
5. CORS + CORS_ORIGINS env
6. Frontend switch: `VITE_API_BASE_URL=http://localhost:8000/api`, MSW оставляем dev fallback
7. Integration test: frontend делает реальный fetch к backend через TestClient

Ponytail: ship step 1-2-3 как MVP, step 4 — следующий change если нужен.
