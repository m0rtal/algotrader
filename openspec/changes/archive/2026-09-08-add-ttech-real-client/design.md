# Design: add-ttech-real-client

## Stack

- **SDK:** `t-tech-investments==1.49.3` (renamed from the archived
  `tinkoff-investments` package; new home is T-Bank's GitLab PyPI mirror
  at `https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`)
- **Module path:** `t_tech.invest` (PyPI name uses dashes, Python module
  uses underscores — standard pip-normalised form)
- **Async runtime:** unchanged — already on `asyncio` + FastAPI
- **Tests:** `pytest` + `pytest-asyncio`, gated integration tests via
  `RUN_SANDBOX_INTEGRATION=1`

## Layout

```
apps/api/src/algotrader_api/ingestion/
  client.py              — Protocol + make_client() factory (unchanged contract)
  real_client.py         — RealTinkoffClient wrapper (rewritten against t-tech SDK)
  real_client_convert.py — _acct_to_dict / _share_to_dict / _candle_to_dict etc.
                            (NEW, extracted from real_client.py)
  fake_client.py         — InMemoryTinkoffClient (unchanged)

apps/api/tests/ingestion/
  test_real_client.py    — mocked SDK unit tests (existing, ~12 tests,
                            minor mock attribute refresh)
  test_real_sandbox.py   — NEW, gated by RUN_SANDBOX_INTEGRATION env var
```

`real_client.py` shrinks from 230 lines to ~90 lines (just the wrapper,
lazy import, target dispatch, async context). Converters move out.

## Data Flow

### `make_client(target=...)` decision tree

```
make_client(sqlite_path=db_path, use_fake=None, target=None)
│
├─ use_fake=True OR env ALGOTRADER_INGEST_FAKE=1
│   └─ return InMemoryTinkoffClient()            # existing path
│
└─ use_fake=False (or None with env unset)
    ├─ load broker_token from db.secrets          # existing
    ├─ if token missing → raise RuntimeError      # existing
    ├─ resolve target:
    │   ├─ target arg wins (explicit)
    │   ├─ else BrokerSettings.environment        # NEW: read from settings
    │   └─ else "sandbox" (default — safe)
    └─ return RealTinkoffClient(token=token, target=target)
        │
        └─ AsyncClient(token, target=INVEST_GRPC_API_SANDBOX)   # lazy
```

### `RealTinkoffClient` method mapping

| Our Protocol method                     | SDK call (t_tech.invest 1.49.3)                                                                                                                                           |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_accounts()`                        | `await client.users.get_accounts(GetAccountsRequest())`                                                                                                                   |
| `get_shares()`                          | `await client.instruments.shares(GetInstrumentsRequest(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE, instrument_type=InstrumentType.INSTRUMENT_TYPE_SHARE))` |
| `get_bonds()`                           | same with `INSTRUMENT_TYPE_BOND`                                                                                                                                          |
| `get_etfs()`                            | same with `INSTRUMENT_TYPE_ETF`                                                                                                                                           |
| `get_futures()`                         | same with `INSTRUMENT_TYPE_FUTURE`                                                                                                                                        |
| `get_options()`                         | same with `INSTRUMENT_TYPE_OPTION`                                                                                                                                        |
| `get_candles(figi, from, to, interval)` | `await client.market_data.get_candles(GetCandlesRequest(instrument_id=figi, from_=from, to=to, interval=CandleInterval[interval]))`                                       |
| `aclose()`                              | `await self._client.close()` if `_client is not None`                                                                                                                     |

The instrument-type filter is **NEW** compared to the old SDK's
`shares()` / `bonds()` etc. methods — the new SDK uses one
`GetInstrumentsRequest` with a discriminator. The wrappers preserve the
existing `get_shares()` / `get_bonds()` / etc. names so
`universe.discover_universe()` doesn't need changes.

### Sandbox vs production

| target                | gRPC host                            | account behavior                                                                                      |
| --------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| `"sandbox"` (default) | `sandbox-invest-public-api.tbank.ru` | virtual ~10M RUB, instruments mirror live MOEX on 15-min delay, orders simulated not sent to exchange |
| `"production"`        | `invest-public-api.tbank.ru`         | real money, real orders                                                                               |

Per user direction 2026-09-07: **default is sandbox**. Switching to
production requires an explicit `BrokerSettings.environment = 'production'`
in Settings; even then a guard prevents it for the first 6 months of
paper trading (covered by a separate change, not this one).

### Sandbox integration test gating

```
RUN_SANDBOX_INTEGRATION=1  → tests/ingestion/test_real_sandbox.py runs
otherwise                  → marked skip with reason "sandbox tests disabled"
```

Token source for the integration test: read from the app's own
`secrets.broker_token` row in `state.db` — never from env, never from
`.env`, never hardcoded. The test calls
`db.secrets.get_broker_token(settings.sqlite_path)` and fails with a clear
message if no token is present.

## Risks

1. **Breaking change to `InstrumentStatus.INSTRUMENT_STATUS_BASE`.**
   The new SDK renamed/removed some enum values. Mitigated by: the
   constant name is unchanged in `t_tech.invest.InstrumentStatus` per
   the probe — if it diverges at runtime, the sandbox test will catch
   it on the first real run.

2. **`from_` kwarg confusion in `GetCandlesRequest`.** Tinkoff uses
   `from_` (trailing underscore) because `from` is a Python keyword.
   Already a known pitfall (per spec reference). The wrapper passes
   `from_=_iso(date_from)` so callers don't see the underscore.

3. **gRPC stub drift across SDK versions.** `t-tech-investments` bundles
   its own `.proto` files and ships `mypy-protobuf` types; no separate
   `grpcio-tools` generation needed. Pinned to `>=1.49.3` so we get a
   stable surface; lockfile catches bumps.

4. **PyPI quarantine confusion.** A stale `t-tech-investments==0.3.3` is
   on public PyPI in quarantine (cannot install). Newer versions
   (`>=1.0`) live ONLY on the GitLab mirror. The `[[tool.uv.index]]`
   pin prevents `uv sync` from accidentally pulling the quarantined
   version when the index is misconfigured.

5. **Pyright LSP diagnostics.** Until `pyright` is run against the new
   SDK types, unknown attribute warnings on `client.users.get_accounts`
   etc. will appear in the conftest. Sandbox tests cover this on first
   real run; ignore pre-test LSP noise.

## Testing Strategy

- **Unit tests (always-on):** 12 existing mocked tests in
  `test_real_client.py`. Each creates a fake `AsyncClient` with
  preshaped response objects, calls the wrapper method, asserts the
  dict shape matches what downstream `universe.upsert_instruments` and
  `bars.run_bars_phase` expect. Mock objects use `unittest.mock.Mock`
  with `spec=AsyncClient` so wrong attribute access raises.
- **Integration tests (gated):** 5 tests in `test_real_sandbox.py`,
  run only with `RUN_SANDBOX_INTEGRATION=1`. They hit the real Tinkoff
  sandbox gRPC endpoint, asserting real MOEX data comes back. Skip
  reason is "sandbox tests disabled" by default.
- **Coverage:** `real_client.py` stays in `tool.coverage.run.omit`
  (sandbox-only code, no point counting it). The new converters file
  is NOT omitted — fully exercised by mocked unit tests.

## Sandbox vs Production Method Differences

User 2026-09-07: "в sandbox отличаются методы" — confirmed by probing
`t_tech.invest.services` and `constants`:

- **Same for both:** `GetAccounts`, `GetInstrumentsRequest`, `GetCandles`,
  `InstrumentStatus.INSTRUMENT_STATUS_BASE`, `CandleInterval.*`.
- **Sandbox-only:** `OpenSandboxAccountRequest`, `CloseSandboxAccountRequest`,
  `SandboxPayInRequest` (sandbox account lifecycle + virtual balance top-up).
- **Production-only:** Real `PostOrder` with money, real `GetOperationsByCursor`
  with ledger entries. In sandbox, `GetOperationsByCursor` returns the
  simulated fill log only.

For this change (universe + candles only), the method overlap is 100% —
both targets return the same data structure. We DO NOT wrap sandbox-only
methods yet; if/when virtual-balance funding is needed, it's a separate
change. We also do NOT guard against the production-only methods being
called in sandbox (they'd just fail at the gRPC layer, which is the SDK's
job to surface).

## Decision Log

- **Default target = sandbox** (user, 2026-09-07) — picked over
  env-var-driven (`ALGOTRADER_TINKOFF_TARGET`) and over always-defaulting-
  to-settings. Reasoning: zero-config safe default; production requires
  an explicit flip through Settings UI which can have a separate
  confirm-dialog guard.
- **Integration tests gated by env var** (user, 2026-09-07) — picked
  over "always-on with mock token" and over "every CI run hits real
  sandbox". Reasoning: sandbox has rate limits (15 req/min per method),
  so naive CI would burn through quota. Gating lets local dev run them
  on-demand with a real token.
- **Converters extracted to separate file** — picked over keeping them
  inline in `real_client.py`. Reasoning: the wrapper file was 230 lines
  and the converters were 100 of those with no shared state; extracting
  keeps each file under 100 lines and lets the converters get coverage
  counted (they were in the omitted file before).
