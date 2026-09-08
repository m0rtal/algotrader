# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: SDK Source

The ingestion layer SHALL use `t-tech-investments>=1.49` (renamed from
the archived `tinkoff-investments` package) installed from T-Bank's GitLab
PyPI mirror at
`https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple`.

#### Scenario: pyproject declares the new SDK with mirror index

- GIVEN the `apps/api/pyproject.toml` declares dependencies
- WHEN `uv sync --extra dev` is run
- THEN `t-tech-investments>=1.49` is resolved from the `tbank` index and
  installed in the project venv
- AND the public PyPI is NOT used as a source for `t-tech-investments`
  (the public PyPI version `0.3.3` is quarantined)

### Requirement: RealTinkoffClient Targets Sandbox by Default

The `RealTinkoffClient` SHALL default to `target="sandbox"` (which maps
to `INVEST_GRPC_API_SANDBOX = "sandbox-invest-public-api.tbank.ru"`) so
that live fetch operations do not execute real trades against the
production broker without explicit configuration.

#### Scenario: Default target resolution when no environment override

- GIVEN the operator has not set `BrokerSettings.environment`
- AND no `target` argument is passed to `make_client()`
- WHEN `RealTinkoffClient(token=...)` is constructed
- THEN it targets `INVEST_GRPC_API_SANDBOX`
- AND any order operations that would require real money are not
  executed (sandbox orders are simulated)

#### Scenario: Explicit target override

- GIVEN the operator has set `BrokerSettings.environment = "production"`
- WHEN `RealTinkoffClient(token=...)` is constructed via
  `make_client(sqlite_path=..., target=None)`
- THEN the wrapper reads `BrokerSettings.environment` and targets
  `INVEST_GRPC_API` (`invest-public-api.tbank.ru`)

### Requirement: SDK Method Wrappers

The `RealTinkoffClient` SHALL provide exactly the methods required by the
ingestion pipeline: `get_accounts`, `get_shares`, `get_bonds`, `get_etfs`,
`get_futures`, `get_options`, `get_candles`, and `aclose`. Each method
SHALL return plain Python `list[dict]` regardless of the underlying gRPC
response shape, so that downstream code in `universe.discover_universe()`
and `bars.run_bars_phase()` does not depend on the SDK type hierarchy.

#### Scenario: Universe discovery via t-tech-investments

- GIVEN a valid sandbox broker token is stored in the app's SQLite
  `secrets.broker_token` row
- WHEN `RealTinkoffClient.get_shares()` is called
- THEN it issues `await client.instruments.shares(GetInstrumentsRequest(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_BASE, instrument_type=InstrumentType.INSTRUMENT_TYPE_SHARE))`
- AND returns a `list[dict]` where each dict has keys
  `ticker`, `figi`, `class`, `name`, `currency`, `lot_size`,
  `isin`, `sector`

#### Scenario: Daily bars fetch via t-tech-investments

- GIVEN a known share FIGI (e.g. SBER) is in scope
- WHEN `RealTinkoffClient.get_candles(figi="BBG004730N88", date_from="2024-01-01", date_to="2024-12-31", interval="CANDLE_INTERVAL_DAY")` is called
- THEN it issues `await client.market_data.get_candles(GetCandlesRequest(instrument_id="BBG004N88", from_="2024-01-01", to="2024-12-31", interval=CandleInterval.CANDLE_INTERVAL_DAY))`
- AND returns a `list[dict]` where each dict has keys `ts`, `open`,
  `high`, `low`, `close`, `volume`

### Requirement: Sandbox Integration Tests Are Gated

The system SHALL provide integration tests that exercise
`RealTinkoffClient` against the real Tinkoff sandbox gRPC endpoint,
gated by the `RUN_SANDBOX_INTEGRATION=1` environment variable so that
they do not run during normal `pytest` invocations.

#### Scenario: Sandbox tests are skipped by default

- GIVEN `RUN_SANDBOX_INTEGRATION` is not set
- WHEN `uv run pytest tests/` is run
- THEN all tests in `tests/ingestion/test_real_sandbox.py` are skipped
  with reason "sandbox tests disabled"

#### Scenario: Sandbox tests run when enabled and token is present

- GIVEN `RUN_SANDBOX_INTEGRATION=1`
- AND a sandbox broker token is stored in `secrets.broker_token`
- WHEN `uv run pytest tests/ingestion/test_real_sandbox.py -v` is run
- THEN 5 sandbox tests execute against the real Tinkoff sandbox endpoint
- AND they pass with real MOEX data (e.g. ≥1 instrument for `get_shares`,
  ≥1 candle for `get_candles`)

### Requirement: Real Client Remains Coverage-Exempt

`real_client.py` SHALL remain in `tool.coverage.run.omit` so that the
relaxed 92% lines coverage gate is not broken by sandbox-gated code
paths. The new `real_client_convert.py` (gRPC → dict converters) is
NOT omitted because it is fully exercised by mocked unit tests.

#### Scenario: Coverage report excludes real_client.py

- GIVEN `apps/api/pyproject.toml` declares
  `tool.coverage.run.omit = ["src/algotrader_api/ingestion/real_client.py"]`
- WHEN `uv run pytest tests/ --cov=algotrader_api --cov-report=term` is run
- THEN `real_client.py` is listed with `100%` or marked excluded
- AND `real_client_convert.py` is listed with ≥90% line / branch coverage
- AND total project coverage is ≥92%
