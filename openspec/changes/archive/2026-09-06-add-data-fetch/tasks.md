# add-data-fetch — tasks

## 1. Storage & dependencies

- [ ] 1.1. Add deps to `apps/api/pyproject.toml`: `tinkoff-investments>=0.2.0,<0.3.0` (via GitLab index), `aiolimiter>=1.1.0`, `pendulum>=3.0.0`. Update `uv.lock`.
- [ ] 1.2. Confirm SDK availability by running `uv pip install` from the GitLab index URL (with auth).
- [ ] 1.3. Add migration `002_instruments_and_pipeline.sql` with `instruments` and `pipeline` tables.
- [ ] 1.4. Update `seed/synth.py` to be **opt-in only** — set via `ALGOTRADER_SYNTH_SEED=1` env var (default off when worker has run).

## 2. Tinkoff client abstraction

- [ ] 2.1. Implement `ingestion/client.py` — `TinkoffClient` Protocol with all methods we use: `users.get_accounts`, `instruments.shares/bonds/etfs/futures/options`, `market_data.get_candles`.
- [ ] 2.2. Implement `ingestion/fake_client.py` — `InMemoryTinkoffClient` with seeded response data + recording of all calls for assertion in tests.
- [ ] 2.3. Implement `RealTinkoffClient` — wraps `tinkoff.invest.AsyncClient` with token from `~/.hermes/secrets/tinkoff_token`.
- [ ] 2.4. Factory `make_client()` in `ingestion/client.py` — picks real or fake based on `ALGOTRADER_INGEST_FAKE=1` (test) vs default real.
- [ ] 2.5. Tests: protocol compliance (NotImplementedError on missing methods), fake client roundtrip, real client constructor error paths.

## 3. Rate limiting & retry

- [ ] 3.1. Implement `ingestion/rate_limit.py` — `RateLimiter` class wrapping `aiolimiter.AsyncLimiter(14, 60)` per SDK method. Methods: `acquire(method_name)`, `release_on_error()`, `release_on_success()`.
- [ ] 3.2. Implement `ingestion/retry.py` — `with_retry(coro, max_attempts=5, initial_delay=1.0, max_delay=60.0)`. Detects `RESOURCE_EXHAUSTED` (gRPC code 8) and HTTP 429, applies exponential backoff. Resets on 5 min of 2xx.
- [ ] 3.3. Tests: rate limiter allows exactly 14 calls in 60 sec, blocks 15th; retry halves interval on success; retry backs off on errors.

## 4. Universe discovery

- [ ] 4.1. Implement `ingestion/universe.py` — `discover_universe(client) → list[Instrument]`. Calls `shares/bonds/etfs/futures/options` with `INSTRUMENT_STATUS_BASE`. Filters by class — we ingest bars only for shares+etfs in MVP; bonds/futures/options tracked in `instruments` for visibility.
- [ ] 4.2. Implement SQLite persistence — `upsert_instruments(rows)` using INSERT OR REPLACE. Batch in transactions of 100.
- [ ] 4.3. Tests: FakeTinkoffClient returns canned responses, assert all 4 classes are persisted, dedup works.

## 5. Bars ingestion

- [ ] 5.1. Implement `ingestion/bars.py` — `fetch_bars_for_instrument(client, inst, last_date, history_years) → int (rows_written)`. Uses DuckDB to read existing parquet, gets max date, calls SDK with `from=last_date+1 OR from=today-history_years`.
- [ ] 5.2. Implement atomic parquet write — DuckDB `CREATE TABLE temp_bars AS SELECT * FROM ...` then `COPY temp_bars TO '<temp>.parquet'` then `os.replace(temp, final)`. No torn writes.
- [ ] 5.3. Implement `run_bars_phase(client, instruments, history_years) → int` — orchestrator over all instruments with rate limiter + retry. Emits progress events every 10 instruments.
- [ ] 5.4. Tests: first fetch writes new file; second fetch appends; atom write (no torn files even on simulated crash); retry on RESOURCE_EXHAUSTED.

## 6. Pipeline status tracking

- [ ] 6.1. Implement `ingestion/pipeline.py` — `start_phase(name) → run_id`, `end_phase(run_id, status, rows, detail)`, `latest_per_phase() → list[PhaseStatus]`. Uses SQLite `pipeline` table.
- [ ] 6.2. Wrap each phase (universe, bars) with start/end lifecycle.
- [ ] 6.3. Tests: phase lifecycle correctness, idempotent on crash (re-run picks up where left off).

## 7. Worker entrypoint

- [ ] 7.1. Implement `apps/api/worker.py`:
  - Parse `argv[1]` for mode (`scheduled` | `manual`)
  - Read token from `~/.hermes/secrets/tinkoff_token`
  - Create TinkoffClient (real or fake based on env)
  - Run pipeline phases: discover_universe → fetch_bars
  - On exception: log structured error, mark phase err, exit code 2 (systemd retries)
  - On success: exit code 0
- [ ] 7.2. Implement systemd service unit `ops/systemd/algotrader-fetch.service`:
  - `Type=oneshot`
  - `ExecStart=/home/hermes/algotrader/apps/api/.venv/bin/python -m algotrader_api.worker scheduled`
  - `WorkingDirectory=/home/hermes/algotrader/apps/api`
  - `Restart=on-failure`, `RestartSec=5min`, `StartLimitBurst=3`
  - `Environment=PYTHONPATH=/home/hermes/algotrader/apps/api/src`
- [ ] 7.3. Implement systemd timer unit `ops/systemd/algotrader-fetch.timer`:
  - `OnCalendar=*-*-* 23:00:00 Europe/Moscow`
  - `Persistent=true`
  - `Unit=algotrader-fetch.service`
- [ ] 7.4. Document install: `systemctl --user link ops/systemd/algotrader-fetch.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now algotrader-fetch.timer`
- [ ] 7.5. Tests: worker with FakeTinkoffClient — verify all phases run, pipeline table updated, exit code 0 on success, exit code 2 on error.

## 8. API routes

- [ ] 8.1. Implement `routes/pipeline.py` — `GET /api/pipeline` returns `PipelineStatus` (latest row per phase).
- [ ] 8.2. Implement `routes/admin.py` — `POST /api/admin/fetch`:
  - Returns 202 immediately with run_id
  - Spawns asyncio task that runs `worker.scheduled()` in-process (background)
  - When `ALGOTRADER_FETCH_DISABLED=1`, returns 503 with `{"error": "fetch_disabled"}`
- [ ] 8.3. Wire routes in `main.py` include_router calls.
- [ ] 8.4. Tests: pipeline route returns current state; admin route returns 202 then runs phases (verify pipeline table updated after request returns).

## 9. Integration (sandbox)

- [ ] 9.1. Test fixtures: `tests/sandbox/conftest.py` — skip unless `RUN_SANDBOX_INTEGRATION=1`.
- [ ] 9.2. `tests/sandbox/test_universe.py` — fetch real MOEX shares, assert ≥100 instruments, all have figi/class/currency.
- [ ] 9.3. `tests/sandbox/test_bars.py` — fetch SBER last 30 days, assert ≥20 candles, schema matches frontend `BarSchema`.
- [ ] 9.4. Document `RUN_SANDBOX_INTEGRATION=1 uv run pytest tests/sandbox/` in `apps/api/README.md`.

## 10. Frontend integration

- [ ] 10.1. Drop the MSW pipeline handler in `apps/web/src/mocks/handlers.ts` (only when `VITE_API_BASE_URL` points at real backend — check at build time).
- [ ] 10.2. Verify `apps/web/src/features/pipeline/PipelineTab.tsx` already reads `/api/pipeline` via `usePipeline` hook (existing).
- [ ] 10.3. Update `apps/web/src/lib/api.ts` documentation comment: MSW intercepts `/api/*` ONLY when `VITE_API_BASE_URL=/api`.

## 11. Coverage & CI

- [ ] 11.1. `uv run pytest --cov=algotrader_api --cov-report=term` — coverage gate ≥95% on all 4 metrics.
- [ ] 11.2. Coverage of `ingestion/` modules individually ≥95% (critical path, same hard rule as observability).
- [ ] 11.3. CI workflow: add nightly job that sets `RUN_SANDBOX_INTEGRATION=1` and runs sandbox tests against real Tinkoff.

## 12. Docs & ops

- [ ] 12.1. Update root `README.md` — add Tinkoff token setup section, systemd timer install.
- [ ] 12.2. Update `apps/api/README.md` — document worker process, token file location, env vars (`ALGOTRADER_INGEST_FAKE`, `ALGOTRADER_FETCH_DISABLED`, `ALGOTRADER_SYNTH_SEED`).
- [ ] 12.3. Add ADR for fetch design decisions (token file vs env var, cron vs daemon, sandbox first vs live first).
- [ ] 12.4. Husky pre-commit already covers credential scanner — add a check that `*.parquet` files are not staged (via `scripts/scan-credentials.py` extension or new check).
