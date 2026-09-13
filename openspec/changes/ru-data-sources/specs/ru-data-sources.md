# ru-data-sources Specification

## Purpose

This spec extends the existing `data-fetch` ingestion layer
(`apps/api/src/algotrader_api/ingestion/`) with four new official-source
pipelines for the MOEX universe: CBR macro, MOEX ISS indices, Tinkoff
fundamentals, and Tinkoff corporate actions. Each pipeline follows the
existing pattern of `bars.py` → `pipeline.py` → per-source parquet →
DuckDB view. Research rationale: see
`/home/hermes/algotrader_research/report.md` TL;DR §9-§11 and sections
#6, #10, #11.

## Requirements

### Requirement: CBR Macro Daily Fetch

The system SHALL provide a daily-fetch pipeline for the Central Bank
of Russia SOAP service
(`https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx`) that records
the Key Rate, USD/RUB, EUR/RUB, and overnight REPO rate, writes them
to `data/macro/cbr_daily.parquet`, and registers a DuckDB view
`cbr_macro` so downstream queries can JOIN against `bars` by date.

#### Scenario: First fetch populates cbr_daily.parquet

- GIVEN no parquet file exists at `data/macro/cbr_daily.parquet`
- WHEN the operator triggers `POST /api/admin/macro/refresh` with body
  `{from_date: "2024-01-01", to_date: "2026-09-12"}`
- THEN the runner issues one SOAP call to `KeyRate` and one to
  `GetCursOnDate` for each date in the range
- AND each metric is written as one row per date with columns
  `metric VARCHAR, ts DATE, value DOUBLE, source VARCHAR DEFAULT 'cbr'`
- AND a row is inserted into the SQLite `pipeline` table with
  `phase='fetch_macro'`, `status='ok'`, `rows_processed=N`
- AND `data/macro/cbr_daily.parquet` exists and is non-empty

#### Scenario: Incremental fetch only requests missing dates

- GIVEN `cbr_daily.parquet` last row has `ts = '2026-09-11'`
- AND today is `2026-09-12`
- WHEN the operator triggers `POST /api/admin/macro/refresh` without
  body
- THEN the runner requests `from_date = '2026-09-12'` and
  `to_date = today`
- AND existing rows are preserved (parquet is appended via DuckDB
  insert, not overwritten)

#### Scenario: SOAP fault does not corrupt existing parquet

- GIVEN CBR returns a SOAP fault during a refresh
- WHEN the runner catches the fault
- THEN `cbr_daily.parquet` is unchanged (write happens in a single
  DuckDB transaction, rollback on error)
- AND the SQLite `pipeline` row is updated with `status='err'` and
  `detail='<fault_code>: <fault_string>'`

#### Scenario: Regime classification endpoint computes from latest values

- GIVEN `cbr_daily.parquet` contains rows for the last 90 days
- WHEN the operator calls `GET /api/macro/regime`
- THEN the response includes
  `key_rate_latest`, `key_rate_change_3m`, `usd_rub_latest`,
  `usd_rub_volatility_30d` (computed as 30d rolling stdev of
  daily log returns of USDRUB), and a categorical
  `regime_label ∈ {"tightening", "easing", "stable"}` based on
  `key_rate_change_3m` sign

### Requirement: MOEX ISS Indices Daily Fetch

The system SHALL provide a daily-fetch pipeline for the MOEX ISS REST
API (`iss.moex.com`) that records daily closes for indices `IMOEX`,
`RGBI`, and `MOEXOG` (oil & gas sector), writes to
`data/indices/moex_daily.parquet`, and registers a DuckDB view
`moex_indices`.

#### Scenario: First fetch populates moex_daily.parquet

- GIVEN no parquet file exists at `data/indices/moex_daily.parquet`
- WHEN the operator triggers `POST /api/admin/indices/refresh` with
  body `{from_date: "2024-01-01", to_date: "2026-09-12"}`
- THEN the runner issues REST calls to
  `https://iss.moex.com/iss/engines/stock/markets/index/securities.json?date=YYYY-MM-DD`
- AND writes columns `index_code VARCHAR, ts DATE, close DOUBLE,
  volume BIGINT, source VARCHAR DEFAULT 'moex_iss'`
- AND a row is inserted into `pipeline` with
  `phase='fetch_indices', status='ok', rows_processed=N`

#### Scenario: Rate limit on MOEX ISS

- GIVEN MOEX ISS allows 60 requests/minute per IP
- WHEN the runner is fetching 365 calendar days of indices
- THEN requests are batched at most 50 per minute to stay under the
  limit
- AND total runtime ≤ 8 minutes

### Requirement: Tinkoff Fundamentals Coverage

The system SHALL provide a per-ticker fundamentals fetch wrapping
the existing Tinkoff SDK's `GetAssetFundamentals` RPC, write per-ticker
parquet under `data/fundamentals/<ticker>.parquet`, register a DuckDB
view `ticker_fundamentals`, and return a coverage report to the
operator.

#### Scenario: First fetch writes per-ticker fundamentals

- GIVEN the algotrader universe has 50 tickers from
  `instruments` table
- WHEN the operator triggers
  `POST /api/admin/fundamentals/refresh`
- THEN for each ticker the runner issues one
  `client.instruments.get_asset_fundamentals` RPC
- AND writes a parquet file at
  `data/fundamentals/<ticker>.parquet` with columns
  `ticker, ts, pe_ratio, pb_ratio, eps, ev_ebitda, roe, roa,
  debt_equity, revenue, net_income, source VARCHAR DEFAULT 'tinkoff'`
- AND missing fields are stored as NULL (not raised as error)
- AND the endpoint response body is
  `{ticker: {total_fields, non_null_fields, coverage_pct}, ...}`

#### Scenario: Coverage report surfaces gaps for follow-up DIY

- GIVEN 50 tickers have been fetched
- WHEN the operator calls `GET /api/fundamentals/coverage`
- THEN the response returns the same per-ticker coverage map plus a
  top-level `summary.aggregate_coverage_pct` and
  `summary.tickers_below_60pct` count
- AND `summary.aggregate_coverage_pct >= 60` triggers no further
  action
- AND `summary.aggregate_coverage_pct < 60` is the documented gate
  for opening an `e-disclosure.ru` follow-up change

#### Scenario: Quarterly cadence is the default

- GIVEN the runner processes a fundamentals refresh
- WHEN the per-ticker parquet already has a row for the most recent
  reported quarter
- THEN the runner skips that ticker (idempotent; quarterly data does
  not change intra-quarter)
- AND the SQLite `pipeline` row records
  `detail='skipped: fresh quarterly data'`

### Requirement: Tinkoff Corporate Actions Calendar

The system SHALL provide an event-driven fetch for Tinkoff dividends
and corporate actions, write to
`data/actions/corporate_actions.parquet`, register a DuckDB view
`corporate_actions`, and serve an "upcoming" endpoint for the UI.

#### Scenario: Fetch dividends for upcoming 30 days

- GIVEN today is `2026-09-12`
- WHEN the operator calls
  `GET /api/corporate_actions/upcoming?days=30`
- THEN the response lists all dividends with `ex_date <= today + 30`
- AND each row includes `ticker, ex_date, pay_date, amount, currency,
  declared_at, source VARCHAR DEFAULT 'tinkoff'`
- AND only rows for figis present in the algotrader `instruments`
  table are returned

#### Scenario: One-time backfill populates the full history

- GIVEN `corporate_actions.parquet` is empty
- WHEN the operator triggers
  `POST /api/admin/corporate_actions/refresh` with
  body `{from_date: "2020-01-01"}`
- THEN the runner calls `client.instruments.get_dividends` for each
  figi with `from_=from_date`
- AND writes all returned events to the parquet
- AND rate-limits at 14 req/min (existing Tinkoff cap)

### Requirement: Pipeline Phase Tracking

The existing `pipeline.py` PhaseName literal SHALL be extended to
include `fetch_macro`, `fetch_indices`, `fetch_fundamentals`,
`fetch_corporate_actions` so each new pipeline writes progress rows
that surface through `GET /api/pipeline`.

#### Scenario: Each new phase writes a SQLite pipeline row

- GIVEN a new pipeline phase is started
- WHEN the runner calls `start_phase(db_path, 'fetch_macro')`
- THEN a row is inserted into `pipeline` with
  `phase='fetch_macro'`, `status='idle'`
- AND when the phase finishes, the same row is updated with
  `status='ok'|'warn'|'err'`, `rows_processed`, `detail`

#### Scenario: Concurrent runs of different phases are allowed

- GIVEN `fetch_macro` is currently running
- WHEN the operator triggers `fetch_indices`
- THEN the new run starts in parallel
- AND concurrent runs of the same phase are refused (HTTP 409, same
  as the existing bars backfill behaviour)

### Requirement: Test Coverage ≥95%

Every new module SHALL have ≥95% line coverage per the algotrader
hard rule (`AGENTS.md` / user preference). TDD: tests written before
implementation.

#### Scenario: New modules pass the coverage gate

- GIVEN `apps/api/src/algotrader_api/ingestion/sources/cbr.py` exists
- AND `tests/ingestion/sources/test_cbr.py` exists
- WHEN `uv run pytest --cov=algotrader_api.ingestion.sources --cov-fail-under=95`
  is run
- THEN the command exits 0 and coverage is reported ≥95%

#### Scenario: All four source clients have Protocol-based fakes

- GIVEN `sources/_client_protocol.py` defines `CbrProtocol`,
  `MoexProtocol`, `TinkoffProtocol`
- AND `tests/ingestion/sources/conftest.py` provides
  `FakeCbrClient`, `FakeMoexClient`, `FakeTinkoffClient`
- WHEN tests run
- THEN no test requires network or sandbox credentials — every
  external dependency is mocked at the Protocol boundary