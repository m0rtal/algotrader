# add-backend-api — tasks

## 1. Skeleton

- [ ] 1.1. Create `apps/api/pyproject.toml` (uv-managed, deps: fastapi, uvicorn[standard], duckdb, pydantic, pydantic-settings, structlog, opentelemetry-api/sdk/exporter-otlp/instrumentation-fastapi/instrumentation-sqlite3, python-dateutil, python-dotenv)
- [ ] 1.2. Create `apps/api/src/algotrader_api/` package structure + `observability/` subpackage
- [ ] 1.3. Implement `config.py` — pydantic-settings with OTel env vars + log sampling
- [ ] 1.4. Implement `main.py` — FastAPI app factory + lifespan + `/health` endpoint
- [ ] 1.5. Implement `observability/` module (see section 1A below)
- [ ] 1.6. `uv sync` from root
- [ ] 1.7. Run `uvicorn algotrader_api.main:app --reload` locally, hit `/health`, verify logs+trace
- [ ] 1.8. Add `apps/api/README.md` with quickstart including OTel collector URL config

## 1A. Observability Module

- [ ] 1A.1. Implement `observability/tracing.py` — setup_tracing() configures TracerProvider with OTLP gRPC exporter + service.name + resource attributes
- [ ] 1A.2. Implement `observability/logging.py` — setup_logging() configures structlog + OTel log handler + stdout JSON fallback + structlog-scrubbers integration
- [ ] 1A.3. Implement `observability/correlation.py` — ContextVar `correlation_id`, FastAPI middleware reads/generates X-Correlation-ID, sets contextvar, adds to response header
- [ ] 1A.4. Implement `observability/scrubbers.py` — redact_token_fields(), mask_account_id(), truncate_long_strings(), drop_health_noisy() structlog processors
- [ ] 1A.5. Implement `observability/middleware.py` — latency middleware measures request duration_ms, attaches to active OTel span, logs info event
- [ ] 1A.6. Implement `observability/instrumentation.py` — instrument_db_query() context manager wrapping SQL execution with OTel span (db.statement, db.system, db.duration_ms, db.row_count)
- [ ] 1A.7. Wire OTel FastAPI instrumentation in `main.py` lifespan
- [ ] 1A.8. Configure structlog → OTel log exporter mapping (SeverityNumber mapping: DEBUG=5, INFO=9, WARN=13, ERROR=17)
- [ ] 1A.9. Tests: correlation_id propagation (header in/out), generation when missing, token field redaction, account_id masking, health sampling drop, span attributes for DB queries, OTel exporter receives logs
- [ ] 1A.10. Test degraded mode: stop collector → logs still go to stdout + single warning event

## 2. Storage Layer

- [ ] 2.1. Implement `db/sqlite.py` — connection factory, WAL mode, migrations runner, **OTel-instrumented** execute() через `observability.instrumentation.instrument_db_query()`
- [ ] 2.2. Implement `db/migrations/001_init.sql` — settings + signals + trades + portfolio + logs tables
- [ ] 2.3. Implement `db/duck.py` — DuckDB connection, `query_bars(ticker, from, till)` helper, **OTel span** на каждый query
- [ ] 2.4. Create `data/` directory, gitkeep + .gitignore entry for `data/bars/`, `data/state.db`
- [ ] 2.5. Write unit tests for SQLite (concurrent access, WAL mode active, span emission)
- [ ] 2.6. Write unit tests for DuckDB query helper (parquet roundtrip, span attributes)

## 3. Settings Endpoints

- [ ] 3.1. Implement `routes/settings.py` — GET /api/settings with 404 → defaults
- [ ] 3.2. Implement PUT /api/settings — validate Zod schema (reuse from `@algotrader/shared`), version check (409 on mismatch)
- [ ] 3.3. Implement DELETE /api/settings — drop row, return 204
- [ ] 3.4. Write tests: get empty → defaults, get after put → stored, put bad schema → 400, put wrong version → 409, delete → 204 + subsequent get returns defaults
- [ ] 3.5. Token never logged (structlog scrubber + verified by test)
- [ ] 3.6. Settings PUT создаёт OTel span `settings.put` с attributes (version_old, version_new, section_keys, correlation_id)
- [ ] 3.7. Settings PUT conflict (409) логируется на WARN level со structured event "settings.conflict"
- [ ] 3.8. Settings DELETE создаёт span `settings.delete`, логирует event "settings.reset"

## 4. Seed Data

- [ ] 4.1. Implement `seed/synth.py` — generate 16 tickers × 252 bars with deterministic seed=42
- [ ] 4.2. Bars writer: parquet with zstd, columns: ts, open, high, low, close, volume, adj_close
- [ ] 4.3. State writer: populate SQLite signals, trades, portfolio, logs, settings, folds
- [ ] 4.4. Wire to lifespan: if `data/bars/` empty → run seed
- [ ] 4.5. Tests: seed produces expected counts, deterministic across runs

## 5. Bars Endpoint

- [ ] 5.1. Implement `routes/bars.py` — GET /api/bars/{symbol} via DuckDB
- [ ] 5.2. 404 if ticker not in catalog
- [ ] 5.3. Query params: from, till (optional, date range filter)
- [ ] 5.4. Response: `{ticker, bars: [{ts, open, high, low, close, volume}, ...]}` per `@algotrader/shared.BarsSeries`
- [ ] 5.5. Tests: get valid ticker → bars, get unknown → 404, with date range → filtered

## 6. Other Read Endpoints

For each, follow the pattern of MSW handlers in `apps/web/src/mocks/handlers.ts`:

- [ ] 6.1. GET /api/kpis — synth from latest portfolio + bars
- [ ] 6.2. GET /api/regime — synth: trend/range/volatile, IMOEX delta
- [ ] 6.3. GET /api/tickers — list from `data/bars/` directory + metadata
- [ ] 6.4. GET /api/signals — latest from SQLite signals table
- [ ] 6.5. GET /api/trades — last 100 from SQLite trades table
- [ ] 6.6. GET /api/portfolio — latest snapshot from SQLite
- [ ] 6.7. GET /api/folds — synth 5 walk-forward folds
- [ ] 6.8. GET /api/model — synth model metadata
- [ ] 6.9. GET /api/model-features — synth feature importance
- [ ] 6.10. GET /api/pipeline — synth pipeline steps
- [ ] 6.11. GET /api/logs — last 100 from SQLite logs
- [ ] 6.12. Tests per endpoint: schema validation, 200 response, edge cases

## 7. CORS + Config

- [ ] 7.1. Add CORS middleware with whitelist from `ALGOTRADER_CORS_ORIGINS`
- [ ] 7.2. Default origins: `http://localhost:5173`, `http://192.168.1.101:5173`
- [ ] 7.3. Test: request from allowed origin → 200; from disallowed → CORS error
- [ ] 7.4. Ship `apps/api/.env.example`

## 8. Frontend Switch

- [ ] 8.1. Update `apps/web/.env.example` with `VITE_API_BASE_URL=http://localhost:8000/api`
- [ ] 8.2. Frontend reads from `import.meta.env.VITE_API_BASE_URL`, defaults to `/api` (MSW)
- [ ] 8.3. Document in README how to switch MSW ↔ real backend
- [ ] 8.4. Verify: with `VITE_API_BASE_URL=http://localhost:8000/api` and backend running, dashboard shows seed data

## 9. Integration Test

- [ ] 9.1. Add `apps/web/tests/api-integration.test.ts` — TestClient из FastAPI делает реальный fetch (in-process)
- [ ] 9.2. Test: same Zod schema parses both MSW response и backend response
- [ ] 9.3. Test: frontend Settings PUT flows through real backend, persists in SQLite

## 10. Coverage & CI

- [ ] 10.1. Run `pytest --cov=algotrader_api --cov-report=term-missing`
- [ ] 10.2. Enforce ≥95% all 4 metrics в `pyproject.toml` `[tool.coverage]`
- [ ] 10.3. Add `apps/api/Makefile` target `test` для удобства
- [ ] 10.4. Update root `package.json` scripts: `test:api` runs pytest
- [ ] 10.5. Husky pre-commit: `pnpm test:web && uv run pytest` — нет, sequential слишком медленно
- [ ] 10.6. Husky pre-commit: только web (быстро), api — CI
- [ ] 10.7. Observability coverage: `observability/` modules + 100% (critical path, hard rule)
- [ ] 10.8. CI workflow runs pytest с in-memory OTLP collector (через `opentelemetry-sdk-testing`) для assertions на exported spans/logs

## 11. Docs

- [ ] 11.1. Root README: добавить раздел "Backend (Python)" с quickstart
- [ ] 11.2. `apps/api/README.md`: install, env vars, run dev, run tests, run prod
- [ ] 11.3. ADR auto-update (post-commit hook уже есть)
- [ ] 11.4. Observability README: как поднять OTel collector + Loki + Tempo + Grafana через docker-compose (ссылка на `ops/otel-collector/`)
- [ ] 11.5. Grafana dashboard JSON экспортируется в `ops/grafana/dashboards/algotrader.json` (traces + logs + correlation_id drill-down)
- [ ] 11.6. ADR для OpenTelemetry (почему OTel, как связано с другими логами/трейсами)

## 12. Ops (Docker Compose для OTel stack)

- [ ] 12.1. `ops/otel-collector/docker-compose.yml` — otel-collector + loki + tempo + grafana
- [ ] 12.2. `ops/otel-collector/collector-config.yaml` — receivers (otlp gRPC + HTTP), exporters (loki + tempo), pipelines (traces + logs)
- [ ] 12.3. Grafana datasources auto-provisioned (Loki, Tempo, Prometheus optional)
- [ ] 12.4. README с `docker compose up -d` командами
