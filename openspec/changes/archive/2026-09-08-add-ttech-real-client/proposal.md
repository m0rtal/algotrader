# Proposal: add-ttech-real-client

## Why

`apps/api/src/algotrader_api/ingestion/real_client.py` wraps the SDK that
`add-data-fetch` was built around (`tinkoff-investments`, GitLab project 33,
archived, beta chain). That SDK is no longer the canonical one. T-Bank renamed
and republished it as **`t-tech-investments`** at PyPI mirror
`https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`,
version `1.49.3` (verified 2026-09-07).

Until this change ships, the only way to populate `bars` and `instruments`
tables with real MOEX data is `ALGOTRADER_INGEST_FAKE=1`, which goes through
`InMemoryTinkoffClient` and returns synthetic candles. That blocks any live
sanity check against the broker.

## What Changes

1. **`pyproject.toml`** — replace `tinkoff-invest>=1.0.5` (community fork,
   not actually wired) with `t-tech-investments>=1.49.3`. Add the
   `[[tool.uv.index]]` entry pointing at T-Bank's GitLab PyPI mirror.
   Verified install path (no auth required for public projects):

   ```
   uv pip install t-tech-investments \
       --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
   ```

2. **`ingestion/real_client.py`** — rewrite against the new SDK. The module
   path moves from `tinkoff.invest` to `t_tech.invest` (note the
   underscore: PyPI name is `t-tech-investments`, Python module is
   `t_tech.invest`). The client shape is similar — `AsyncClient(token, target=...)`
   — but every RPC call now takes an explicit request object instead of
   kwargs, e.g. `client.users.get_accounts(GetAccountsRequest())` instead
   of `client.users.get_accounts()`. `target` constants live in
   `t_tech.invest.constants` (`INVEST_GRPC_API` for production,
   `INVEST_GRPC_API_SANDBOX` for sandbox, hostnames
   `invest-public-api.tbank.ru` and `sandbox-invest-public-api.tbank.ru`).

3. **`ingestion/real_client_convert.py`** (NEW) — gRPC → dict converters
   (share/bond/etf/future/option/account/candle) move out of
   `real_client.py` to keep the wrapper file focused. Public surface
   unchanged.

4. **`tests/ingestion/test_real_client.py`** — keep existing 12 mocked-SDK
   tests; they assert the wrapper shape, not the SDK internals, so they
   should still pass with minor refresh of mock attribute names
   (`accounts` instead of `accounts.accounts`, `GetAccountsRequest` import,
   etc.).

5. **`tests/ingestion/test_real_sandbox.py`** (NEW) — gated integration
   tests. Run only when `RUN_SANDBOX_INTEGRATION=1` is set in env. Read
   the broker token from the app's own SQLite `secrets` table (via
   `db.secrets.get_broker_token(sqlite_path)`), construct
   `RealTinkoffClient(token=..., target="sandbox")`, call each method,
   assert non-empty results. 5 tests:
   - `test_sandbox_get_accounts_returns_list`
   - `test_sandbox_get_shares_returns_at_least_one_instrument`
   - `test_sandbox_get_candles_returns_ohlcv_rows`
   - `test_sandbox_token_in_db_is_used_by_real_client`
   - `test_sandbox_aclose_cleans_up_async_client`

   These do NOT run in normal `pytest tests/`. They live in the same
   `tests/ingestion/` dir but are skipped by default, matching the
   `add-data-fetch` plan's recipe for sandbox verification.

6. **`docs/superpowers/plans/2026-09-07-ttech-real-client.md`** —
   task-by-task implementation plan (per superpowers writing-plans skill).

## Impact

- **Backend runtime:** one new dep (`t-tech-investments==1.49.3`) pulled
  from T-Bank's GitLab PyPI mirror on every `uv sync`. No auth required
  (public mirror).
- **Frontend:** unchanged. UI already exposes the broker token field; once
  the new wrapper is in, pasting a real sandbox token through Settings →
  Broker → Токен → Save immediately unlocks live fetch via the existing
  `POST /api/admin/fetch` button.
- **Settings page:** no change required. The default `target` is
  `sandbox`, controlled by the existing `BrokerSettings.environment`
  field already in the Zod schema (`'sandbox' | 'production'`). The
  `RealTinkoffClient.__init__` reads `target` from that field — but the
  current `routes/admin.py` doesn't pass it. Fix in this change: pass
  `get_settings().broker.environment` (default `"sandbox"`) into
  `make_client(target=...)`.
- **Tests:** 144 → 144+5 (5 new sandbox tests, gated). Existing 12
  mocked tests need refresh — expect 12→12 still.
- **Coverage:** `real_client.py` stays in `tool.coverage.run.omit`
  (sandbox-gated). The new converters file is NOT omitted — it has
  straightforward branches and is exercised by the 12 mocked tests.

## Non-Goals

- **Trading operations** (`PostOrder`, `CancelOrder`, `GetOrderState`,
  etc.). The MVP only needs read-only universe + candles. Adding order
  methods is a separate change once the strategy layer ships.
- **Streaming subscriptions** (`market_data_stream`). Polling once per
  scheduled run is enough for daily bars; streaming is for the
  intra-day tick path which is post-MVP.
- **Operations / portfolio endpoints.** Same as above — pre-trading.
- **Migrating from existing `tinkoff-invest>=1.0.5` mention in
  pyproject.toml beyond a straight swap.** The community fork was never
  wired into the wrapper; the wrapper imported `tinkoff.invest` directly,
  which never existed in `tinkoff-invest==1.0.5` either (it ships as
  `tinkoff_invest`). Cleanup is just deleting the wrong dep name.
