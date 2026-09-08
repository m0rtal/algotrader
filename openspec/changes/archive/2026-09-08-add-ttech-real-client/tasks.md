# Tasks: add-ttech-real-client

## Section 1: dependency wiring

- [ ] **1.1** Update `apps/api/pyproject.toml`:
  - Replace `"tinkoff-invest>=1.0.5",` with `"t-tech-investments>=1.49.3",`
  - Add `[[tool.uv.index]] name = "tbank" url = "https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple" explicit = false` block with a comment explaining the GitLab mirror
  - Run `uv sync --extra dev` and confirm `t-tech-investments==1.49.3` installed from the tbank index

- [ ] **1.2** Smoke-test the import in the project venv: `uv run python -c "from t_tech.invest import AsyncClient, CandleInterval; print(AsyncClient)"`. Expected: prints `<class 't_tech.invest.clients.AsyncClient'>` without ImportError.

## Section 2: rewrite `real_client.py`

- [ ] **2.1** Create new `apps/api/src/algotrader_api/ingestion/real_client_convert.py` with the converter functions (`_money`, `_quotation`, `_acct_to_dict`, `_share_to_dict`, `_bond_to_dict`, `_etf_to_dict`, `_future_to_dict`, `_option_to_dict`, `_candle_to_dict`, `_to_iso`, `_to_iso_date`). Moved verbatim from `real_client.py` — no logic change.

- [ ] **2.2** Rewrite `apps/api/src/algotrader_api/ingestion/real_client.py`:
  - Lazy import path: `t_tech.invest` (was `tinkoff.invest`)
  - `target="sandbox"` default (was `"sandbox"` — same default, but now sources constants from `t_tech.invest.constants`)
  - Method bodies rewritten against the new SDK's request-object style:
    - `get_accounts`: `await client.users.get_accounts(t_tech.invest.GetAccountsRequest())`
    - `get_shares/bonds/etfs/futures/options`: `await client.instruments.{shares|bonds|etfs|futures|options}(GetInstrumentsRequest(instrument_status=self._status_base, instrument_type=INSTRUMENT_TYPE_*))`
    - `get_candles`: `await client.market_data.get_candles(GetCandlesRequest(instrument_id=figi, from_=_iso(date_from), to=_iso(date_to), interval=...))`
  - Imports `converters` from the new `real_client_convert` module
  - File stays under 100 lines (wrappers + lazy import + target dispatch only)

- [ ] **2.3** Update `apps/api/src/algotrader_api/ingestion/client.py`:
  - Add `target: str | None = None` parameter to `make_client()`
  - Resolution order for `target`: explicit arg → `BrokerSettings.environment` (from SQLite `settings` table) → `"sandbox"`
  - Pass `target` through to `RealTinkoffClient(token=token, target=target)`

- [ ] **2.4** Update `apps/api/src/algotrader_api/routes/admin.py`:
  - In `_run_phases`, read `target` from `settings.broker.environment` (also default `"sandbox"`) and pass it to `make_client(sqlite_path=db_path, use_fake=use_fake, target=target)`
  - No new test needed for this path — the `_run_phases` test already mocks `make_client`.

## Section 3: refresh mocked unit tests

- [ ] **3.1** Update `apps/api/tests/ingestion/test_real_client.py`:
  - Imports: `from t_tech.invest import AsyncClient` (was `from tinkoff.invest`)
  - Mock attribute names: the new SDK returns `response.accounts` as a flat list (was `response.accounts.accounts` chain in some patterns). Verify by running the 12 existing tests — fix any attribute name drift.
  - `interval_enum = getattr(CandleInterval, interval, ...)` — same name, no change.
  - `from_` kwarg in `get_candles` test: pass through the wrapper, do not assume kwarg spelling inside.

- [ ] **3.2** Run `uv run pytest tests/ingestion/test_real_client.py -v`. Expected: all 12 mocked tests pass. If any fail because of mock attribute shape, fix the mock (not the wrapper) — the wrapper contract is the source of truth.

## Section 4: gated sandbox integration tests

- [ ] **4.1** Create `apps/api/tests/ingestion/test_real_sandbox.py`:
  - Top-of-file: `import os; import pytest; pytestmark = pytest.mark.skipif(not os.environ.get("RUN_SANDBOX_INTEGRATION"), reason="sandbox tests disabled")`
  - Helper `_make_real_client()` reads token via `db.secrets.get_broker_token(settings.sqlite_path)`, skips the test with a clear message if no token is set.
  - Test 1: `test_sandbox_get_accounts_returns_list` — `accounts = await client.get_accounts()`; assert `isinstance(accounts, list)`.
  - Test 2: `test_sandbox_get_shares_returns_at_least_one_instrument` — call `get_shares()`, assert at least one entry, assert each has `ticker`/`figi`/`class == "share"`.
  - Test 3: `test_sandbox_get_candles_returns_ohlcv_rows` — pick a known share FIGI from test 2, call `get_candles(figi=..., date_from="2024-01-01", date_to="2024-12-31")`, assert at least one row, assert keys `ts`/`open`/`high`/`low`/`close`/`volume` present.
  - Test 4: `test_sandbox_token_in_db_is_used_by_real_client` — proves the wrapper reads the token from `secrets.broker_token` row (write a known token to DB, construct client, assert no error).
  - Test 5: `test_sandbox_aclose_cleans_up_async_client` — call `aclose()`, assert `_client is None` afterwards.

- [ ] **4.2** Add to `pyproject.toml` `[tool.pytest.ini_options]` a `markers` entry: `markers = ["sandbox: real Tinkoff sandbox integration tests, gated by RUN_SANDBOX_INTEGRATION"]`.

- [ ] **4.3** Run the integration tests once locally with the user's sandbox token to verify they actually pass:

  ```
  RUN_SANDBOX_INTEGRATION=1 \
    ALGOTRADER_SQLITE_PATH=/home/hermes/algotrader/apps/api/data/state.db \
    uv run pytest tests/ingestion/test_real_sandbox.py -v
  ```

  Expected: 5 tests pass against real Tinkoff sandbox. If any fail, fix the wrapper or the test before merging.

- [ ] **4.4** Verify the default `pytest tests/` (no env var) still skips them with the message "sandbox tests disabled".

## Section 5: coverage and CI

- [ ] **5.1** `tool.coverage.run.omit` in `pyproject.toml`: keep `real_client.py` omitted (sandbox-gated). The new `real_client_convert.py` is NOT omitted — it should reach near-100% via the mocked unit tests.

- [ ] **5.2** Run full coverage: `uv run pytest tests/ --cov=algotrader_api --cov-report=term`. Expected: ≥92% (current relaxed gate). The new converters file should be at 100% line / 100% branch since every branch is exercised by the 12 mocked tests.

- [ ] **5.3** Run the full backend test suite, including scanner: `uv run pytest tests/` + `pnpm test:scripts`. Expected: 144 + 13 = 157 backend pass.

- [ ] **5.4** Run web frontend tests untouched: `cd apps/web && pnpm exec vitest run`. Expected: 268/268 pass (no frontend changes in this change).

## Section 6: live end-to-end smoke test

- [ ] **6.1** Restart the backend so the new wrapper is loaded:

  ```
  pkill -9 -f "uvicorn algotrader_api" ; sleep 2
  cd /home/hermes/algotrader/apps/api && \
    PATH=/home/hermes/.local/bin:$PATH ALGOTRADER_INGEST_FAKE=1 \
    uv run uvicorn algotrader_api.main:app --host 0.0.0.0 --port 8000 &
  ```

- [ ] **6.2** With backend running in fake mode (default for this dev host), confirm `GET /api/admin/fetch/status` returns `token_set: false` (no real token in DB) and `fetch_disabled: false`. Endpoint shape unchanged.

- [ ] **6.3** Confirm `POST /api/admin/fetch` with `ALGOTRADER_INGEST_FAKE=1` returns 202 with `token_last4: null` (fake mode skips token check, unchanged from current behavior).

- [ ] **6.4** Stop the fake-mode backend. Restart with real mode: drop `ALGOTRADER_INGEST_FAKE=1`, set a real sandbox token in the DB via `curl -X PUT /api/settings/token`, then `POST /api/admin/fetch`. Expected: 202 with `token_last4: "....ABCD"`. Background task should hit real sandbox; verify by tailing backend logs for `tinkoff.client.real` log line.

## Section 7: commit, push, archive

- [ ] **7.1** OpenSpec validation: `openspec validate add-ttech-real-client --strict`. Expected: "Change 'add-ttech-real-client' is valid".

- [ ] **7.2** Self-review: skim the change against the proposal/design — confirm no requirement is missing. Apply the spec delta to canonical: copy `openspec/changes/add-ttech-real-client/specs/data-fetch/spec.md` to `openspec/specs/data-fetch/spec.md`, drop the `(delta)` suffix, rename `## ADDED Requirements` to `## Requirements`, add a `## Purpose` section. Verify with `openspec validate data-fetch --strict`.

- [ ] **7.3** `git add` all changed files (backend code + tests + OpenSpec change files). `git commit -m "feat(data-fetch): migrate real_client to t-tech-investments SDK"`. Expected: pre-commit hook runs `refresh-codebase-memory.py` and reports new node/edge counts.

- [ ] **7.4** `git push origin main`. Verify with `gh api repos/m0rtal/algotrader/commits | head` that the new commit SHA is on `main`.

- [ ] **7.5** `openspec archive add-ttech-real-client --yes --skip-specs`. Expected: change moves to `openspec/changes/archive/2026-09-07-add-ttech-real-client/`. Verify with `openspec list`.

- [ ] **7.6** Final `uv run pytest tests/ --cov=algotrader_api --cov-report=term` to confirm everything still green after the archive.

## Done means

- [ ] All backlog above is checked
- [ ] Backend coverage ≥92%
- [ ] 5 sandbox tests pass when `RUN_SANDBOX_INTEGRATION=1` and a real sandbox token is in the DB
- [ ] 12 mocked tests still pass without the env var
- [ ] Change archived to `openspec/changes/archive/2026-09-07-add-ttech-real-client/`
- [ ] Live `POST /api/admin/fetch` with a real sandbox token returns 202 and the background task hits `sandbox-invest-public-api.tbank.ru` (verify via log line `tinkoff.client.real`)
