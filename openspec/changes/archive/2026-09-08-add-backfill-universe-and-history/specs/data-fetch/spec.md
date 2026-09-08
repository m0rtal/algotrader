# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: Universe + History Backfill

The system SHALL provide a persistent backfill subsystem that fetches the
live MOEX universe and historical daily bars from the broker sandbox,
writes closed candles to per-ticker parquet, and surfaces live progress
to the UI through Server-Sent Events.

#### Scenario: First run on empty database populates universe and backfills

- GIVEN the `secrets.broker_token` row contains a valid sandbox token
- AND no rows exist in `instrument_metadata`
- WHEN the operator triggers `POST /api/admin/backfill/start` with
  body `{history_years: 5}`
- THEN the runner transitions through states
  `IDLE → DISCOVERING → BACKFILLING → DONE`
- AND the `instruments` table is populated from `get_shares()`,
  `get_bonds()`, `get_etfs()`, `get_futures()`, and `get_options()`
- AND each instrument has an `instrument_metadata` row with
  `last_bar_ts`, `last_backfilled_at`, `total_bars`, and
  `last_run_status = 'ok'`
- AND `bars/<ticker>.parquet` exists for every instrument with at least
  one row

#### Scenario: Incremental run on existing data fetches only fresh bars

- GIVEN the `instrument_metadata` row for figi `BBG004730N88` has
  `last_bar_ts = '2026-09-05'`
- AND the runner's `incremental_threshold_days` setting is `2`
- WHEN the runner processes figi `BBG004730N88`
- THEN it calls `client.get_candles(...)` with `from_ = '2026-09-06'`
  and `to = today`
- AND the new bars are appended to `bars/SBER.parquet`
- AND `instrument_metadata.last_bar_ts` is updated to today's date

#### Scenario: Closed-candle filter excludes the in-progress bar

- GIVEN the SDK returns a candle with `is_complete = False`
- WHEN the runner writes the response to parquet
- THEN that candle is dropped from the output

#### Scenario: Rate limiter throttles SDK calls to 14 req/min per method

- GIVEN the runner is processing 250 tickers via `get_candles()`
- WHEN it issues more than 14 calls in any rolling 60-second window
- THEN the 15th call blocks until a token is available (≥1/14·60 ≈ 4.3s)
- AND no more than 14 calls to `market_data.get_candles` complete per minute

#### Scenario: Rate-limit throttle reduces per-method cap on RESOURCE_EXHAUSTED

- GIVEN the SDK returns gRPC code 8 (RESOURCE_EXHAUSTED) or HTTP 429
- WHEN the runner catches the error
- THEN the per-method cap drops to 10 req/min for 5 minutes
- AND the call retries with exponential backoff (cap 60s)
- AND the next `ticker_progress` event has `payload.throttled = true`

#### Scenario: SSE event stream emits progress during a run

- GIVEN a backfill run is in `BACKFILLING` state
- WHEN the runner finishes one ticker
- THEN it emits a `ticker_progress` event with
  `{figi, ticker, status: "ok", bars_written: N, last_bar_ts: "..."}`
- AND any client subscribed to `GET /api/admin/backfill/events` receives
  it within 1 second

#### Scenario: Refusing a second concurrent run

- GIVEN a backfill run is already in `BACKFILLING` state
- WHEN `POST /api/admin/backfill/start` is called
- THEN the route returns HTTP 409 with body
  `{"detail": {"error": "already_running", "run_id": <current>}}`
- AND no second runner is started

#### Scenario: Stop cancels between tickers

- GIVEN a backfill run is in `BACKFILLING` state
- WHEN `POST /api/admin/backfill/stop` is called
- THEN the runner finishes the current ticker (does not abort
  mid-fetch), then exits before processing the next ticker
- AND the final state is `STOPPING` then `IDLE`
- AND `instrument_metadata` reflects the partial progress

#### Scenario: Crash recovery resumes from instrument_metadata

- GIVEN the runner crashes mid-backfill
- AND `instrument_metadata.last_bar_ts` is populated for tickers 1..N
- WHEN the runner is restarted
- THEN tickers 1..N are skipped (their `last_bar_ts` is fresh)
- AND tickers N+1..end are processed starting from
  `last_bar_ts + 1 day` or full 5 years for never-completed tickers

### Requirement: Rate Limiting

The system SHALL enforce a per-method rate limit at 14 requests per
minute against the Tinkoff sandbox gRPC endpoint, with adaptive
throttling to 10 req/min on RESOURCE_EXHAUSTED, and per-process bucket
state (single-writer MVP).

#### Scenario: Independent buckets per method

- GIVEN the runner is processing both `get_instruments` (universe) and
  `get_candles` (bars) in parallel
- WHEN `get_instruments` exhausts its bucket
- THEN `get_candles` calls continue at their own pace
- AND the two buckets refill independently

#### Scenario: Throttle auto-restores after 5 minutes

- GIVEN `get_candles` triggered a throttle event at time T
- WHEN the runner continues and no further 429/RESOURCE_EXHAUSTED
  errors occur for 5 minutes
- THEN the per-method cap restores to 14 req/min at time T + 5min

### Requirement: Operator Logs

The system SHALL persist every backfill operation (start, stop,
per-ticker success/failure, rate-limit events, errors) to the
`ingestion_logs` table, with columns `ts`, `run_id`, `level`, `figi`,
`message`. The Logs panel in the UI consumes this table through the
SSE endpoint.

#### Scenario: Per-ticker log entry on success

- GIVEN the runner successfully fetches bars for ticker SBER
- WHEN the parquet write completes
- THEN a row is inserted into `ingestion_logs` with
  `level='info', figi='BBG004730N88', message='bars_written=252', run_id=<current>`

#### Scenario: Per-ticker log entry on failure

- GIVEN the SDK raises an exception while fetching bars for ticker
  whose FIGI doesn't exist
- WHEN the runner catches the error
- THEN a row is inserted into `ingestion_logs` with
  `level='error', figi=<bad>, message='<error>', run_id=<current>`
- AND the run continues to the next ticker (failures don't abort)

#### Scenario: Log retention tail

- GIVEN the Logs panel renders the latest events
- WHEN the SSE stream reconnects after a brief network blip
- THEN the panel resyncs from the in-memory event deque (last 100
  events) and continues streaming from the current point
