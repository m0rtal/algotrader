# data-fetch Specification

## Requirements

### Requirement: Tinkoff Token File Storage

The system SHALL read the Tinkoff Invest API token from `~/.hermes/secrets/tinkoff_token`
at worker startup. The file SHALL be owner-readable only (mode 0600) and SHALL
contain a single line with the raw token (no prefix). The token SHALL NOT be
written to `.env`, committed to git, or logged at any log level.

#### Scenario: Token file present and valid

- GIVEN the file `~/.hermes/secrets/tinkoff_token` exists, has mode 0600, and contains a `t.*`-prefixed token
- WHEN the worker starts
- THEN it initializes the Tinkoff client with that token
- AND does NOT log the token value

#### Scenario: Token file missing

- GIVEN the file `~/.hermes/secrets/tinkoff_token` does not exist
- WHEN the worker starts
- THEN it logs an error event `ingest.token.missing` (without the path in the message)
- AND the `discover_universe` phase is marked `status=err`, `detail="token file missing"`
- AND the worker exits with code 2

### Requirement: Daily OHLCV Bars Ingestion

The system SHALL fetch daily OHLCV (open, high, low, close, volume, adj_close)
bars for all MOEX share and ETF instruments from the Tinkoff Invest sandbox API.
Bars SHALL be written as Parquet files at `data/bars/<ticker>.parquet` with zstd
compression, replacing the previous synth seed for the same tickers.

#### Scenario: First run — initial fetch

- GIVEN the instruments table contains N MOEX shares+ETFs
- AND no parquet files exist for them
- WHEN the `fetch_bars` phase runs
- THEN for each instrument it calls `MarketData.GetCandles(figi, from=today-historyYears*365, to=today, interval=CANDLE_INTERVAL_DAY)`
- AND writes bars to `data/bars/<ticker>.parquet` atomically (via temp file + rename)
- AND the worker exits with code 0 when all instruments succeed

#### Scenario: Subsequent run — incremental update

- GIVEN parquet files exist for ticker X with `max(date) = 2024-06-15`
- WHEN the `fetch_bars` phase runs for ticker X
- THEN it calls `GetCandles(figi, from=2024-06-16, to=today, interval=CANDLE_INTERVAL_DAY)`
- AND appends the new bars to the existing parquet file
- AND the resulting parquet contains rows for all dates from `historyYears` ago through today

### Requirement: Per-Method Rate Limiting

The system SHALL limit Tinkoff API calls to 14 requests per minute per SDK method
(safety margin of 1 below the documented 15 req/min limit). On `RESOURCE_EXHAUSTED`
(gRPC code 8) or HTTP 429, the system SHALL apply exponential backoff with a cap at
60 seconds between retries and a maximum of 5 retry attempts.

#### Scenario: Burst of 20 calls in 1 second

- GIVEN the rate limiter is configured for 14 calls / 60 seconds
- WHEN 20 calls to `Shares.GetShares` are issued within 1 second
- THEN the first 14 calls proceed immediately
- AND the remaining 6 calls wait until tokens are replenished (4-second wait on average)

#### Scenario: Adaptive backoff on RESOURCE_EXHAUSTED

- GIVEN a Tinkoff call returns `RESOURCE_EXHAUSTED`
- WHEN retry kicks in
- THEN the next attempt is delayed by `initial_delay * 2^attempt` seconds (1s, 2s, 4s, 8s, 16s, capped at 60s)
- AND after 5 consecutive failures, the operation is abandoned and the phase is marked `err`

### Requirement: Universe Discovery

The system SHALL discover all MOEX instruments at worker startup by calling
`Shares.GetShares`, `Bonds.GetBonds`, `Etfs.GetEtfs`, `Futures.GetFutures`, and
`Options.GetOptions` with `instrument_status=INSTRUMENT_STATUS_BASE`. The discovered
instruments SHALL be persisted to the SQLite `instruments` table using
`INSERT OR REPLACE` keyed by ticker.

#### Scenario: First-time discovery

- GIVEN the `instruments` table is empty
- WHEN the `discover_universe` phase runs
- THEN the system calls all five instrument-class endpoints
- AND persists at least 100 instruments (the full MOEX share universe as of 2026)
- AND the phase is marked `status=ok`, `rows_processed=N`

#### Scenario: Incremental update

- GIVEN the `instruments` table contains 200 rows from a previous run
- AND Tinkoff returns 205 instruments today (5 new listings)
- WHEN the `discover_universe` phase runs
- THEN the table contains 205 rows after the phase
- AND 5 rows were newly inserted

### Requirement: Pipeline Status Visibility

The system SHALL track the status of each pipeline phase (`discover_universe`,
`fetch_bars`) in the SQLite `pipeline` table with `started_at`, `finished_at`,
`rows_processed`, `status`, and `detail`. The `GET /api/pipeline` endpoint SHALL
return the latest row per phase so the frontend Pipeline tab can render real
fetch state instead of mock data.

#### Scenario: Phase lifecycle

- GIVEN a fresh install
- WHEN the worker starts
- THEN a row is inserted in `pipeline` with `phase='discover_universe'`, `started_at=now()`, `status='idle'`
- AND when the phase finishes, the same row is updated with `finished_at=now()`, `status='ok'`, `rows_processed=N`

#### Scenario: Pipeline endpoint reflects last run

- GIVEN the worker has run once with 248 instruments discovered and 310000 bars fetched
- WHEN a client calls `GET /api/pipeline`
- THEN the response contains two phases with `status='ok'`, `rows_processed=248` and `rows_processed=310000`

### Requirement: Manual Fetch Trigger

The system SHALL expose `POST /api/admin/fetch` for ad-hoc worker invocations.
The endpoint SHALL return HTTP 202 with a `run_id` within 1 second, then run the
pipeline phases asynchronously as a background task. When the
`ALGOTRADER_FETCH_DISABLED=1` environment variable is set, the endpoint SHALL
return HTTP 503 with `{"error": "fetch_disabled"}`.

#### Scenario: Manual fetch start

- GIVEN the worker is not currently running
- WHEN a client calls `POST /api/admin/fetch`
- THEN the response is HTTP 202 with `{"run_id": 42, "started_at": "..."}`
- AND a background task starts running `discover_universe` then `fetch_bars`
- AND `GET /api/pipeline` shows the running phases as `status='idle'` then `status='ok'` within minutes

#### Scenario: Fetch disabled by env var

- GIVEN `ALGOTRADER_FETCH_DISABLED=1` is set
- WHEN a client calls `POST /api/admin/fetch`
- THEN the response is HTTP 503 with `{"error": "fetch_disabled", "message": "fetch is disabled by configuration"}`

### Requirement: systemd Timer Schedule

The system SHALL provide a systemd user timer unit `algotrader-fetch.timer`
that triggers the worker daily at 23:00 Moscow time. The associated service
unit `algotrader-fetch.service` SHALL run the worker as a one-shot process with
`Restart=on-failure` and a 5-minute delay between retries (max 3 attempts).

#### Scenario: Timer fires at 23:00 MSK

- GIVEN the user has enabled the timer with `systemctl --user enable --now algotrader-fetch.timer`
- WHEN the system clock reaches 23:00:00 in the `Europe/Moscow` timezone
- THEN systemd starts `algotrader-fetch.service`
- AND the worker fetches all instruments and bars
- AND the next invocation is scheduled for the following day at 23:00:00

#### Scenario: Worker fails — systemd retries

- GIVEN the worker exits with code 2 (error)
- WHEN systemd detects the failure
- THEN after 5 minutes, systemd restarts the service
- AND if the service fails 3 times within the burst interval, systemd stops retrying until the next timer trigger

### Requirement: Synthetic Seed Fallback

The system SHALL fall back to the deterministic synth seed (`apps/api/src/algotrader_api/seed/synth.py`)
when the Tinkoff token file is missing OR when the `ALGOTRADER_SYNTH_SEED=1` environment
variable is set. This allows developer environments without Tinkoff credentials to
still have data on the dashboard.

#### Scenario: Dev environment without token

- GIVEN `~/.hermes/secrets/tinkoff_token` does not exist
- AND `ALGOTRADER_SYNTH_SEED` is unset
- WHEN the API starts (lifespan)
- THEN the synth seed runs and populates 16 tickers × ~180 bars
- AND `GET /health` returns `bars_count=2880`

#### Scenario: Production with real token

- GIVEN `~/.hermes/secrets/tinkoff_token` exists with valid content
- AND the worker has run at least once successfully
- WHEN the API starts
- THEN the synth seed is SKIPPED (the worker has populated real data)
- AND `GET /health` returns `bars_count=N` where N is the total bars across all parquet files

### Requirement: Coverage ≥95%

The system SHALL enforce ≥95% coverage on lines, branches, functions, and statements
across all ingestion modules, including the sandbox integration tests if they
are present.

#### Scenario: Coverage gate enforced

- GIVEN the test suite runs with coverage report
- WHEN the ingestion module coverage drops below 95% on any metric
- THEN pytest exits with non-zero status
