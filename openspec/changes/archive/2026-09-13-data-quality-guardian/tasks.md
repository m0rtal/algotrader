# Tasks: data-quality-guardian

## 1. Health module

- [ ] 1.1 Create `apps/api/src/algotrader_api/data_quality/__init__.py` with
      `HealthIssue` enum + `HealthReport` dataclass
- [ ] 1.2 Create `apps/api/src/algotrader_api/data_quality/health.py`
      with `compute_health(db_path, figi) -> HealthReport` and
      `compute_all(db_path) -> dict[figi, HealthReport]`
- [ ] 1.3 Tests: `apps/api/tests/test_data_quality_health.py` (4
      sub-problems × 4 fixture states × 2 happy/sad paths)

## 2. Recovery module

- [ ] 2.1 Create `apps/api/src/algotrader_api/data_quality/recovery.py`
      with `recover_stale(db_path, runner, reports) -> RecoverySummary`
- [ ] 2.2 Tests: `apps/api/tests/test_data_quality_recovery.py`
      (skip exhausted, skip RATE_LIMITED-only, sort by score)

## 3. Service orchestrator

- [ ] 3.1 Create `apps/api/src/algotrader_api/data_quality/service.py`
      with `run_daily_guardian(db_path, runner) -> GuardianSummary`
- [ ] 3.2 Tests: `apps/api/tests/test_data_quality_service.py`
      (full integration with fixture DB)

## 4. BackfillRunner extension

- [ ] 4.1 Add `BackfillRunner.run_with_queue(figis, ...)` to
      `ingestion/backfill.py` — runs `_backfill_one` over a
      pre-prioritised list (no new retry policy)

## 5. FastAPI endpoint

- [ ] 5.1 Create `apps/api/src/algotrader_api/routes/data_quality.py`
      with `GET /api/data-quality/{symbol}` (drill-down payload)
- [ ] 5.2 Update `routes/backfill.py::pending` to include
      `by_health` bucket and `worst` top-5
- [ ] 5.3 Update `routes/data_reads.py::tickers` row to include
      `health_score`
- [ ] 5.4 Update `main.py` to wire `data_quality.router`
- [ ] 5.5 Tests: `apps/api/tests/test_data_quality_route.py`

## 6. Worker integration

- [ ] 6.1 Update `apps/api/worker.py` to add `guardian` mode that
      calls `run_daily_guardian()`
- [ ] 6.2 Update systemd unit file at
      `/etc/systemd/system/algotrader-worker.timer` to use
      `guardian` mode
- [ ] 6.3 Document the new mode in `apps/api/scripts/`

## 7. Spec apply + archive

- [ ] 7.1 `openspec validate data-quality-guardian --strict`
- [ ] 7.2 Apply delta to canonical `openspec/specs/data-fetch/spec.md`
- [ ] 7.3 `openspec validate data-fetch --strict`
- [ ] 7.4 `openspec archive data-quality-guardian --yes --skip-specs`

## 8. Live verification

- [ ] 8.1 Run `python -m algotrader_api.worker guardian` once
      against the prod DB. Confirm: - `compute_all` returns one report per tradeable figi - `recover_stale` queues the 967 currently-error figis - The pipeline table gets a new row with summary
- [ ] 8.2 `GET /api/data-quality/SBER` returns 200 with
      `health_score: 100`
- [ ] 8.3 Restart the systemd timer; next 23:00 MSK run
      happens automatically
