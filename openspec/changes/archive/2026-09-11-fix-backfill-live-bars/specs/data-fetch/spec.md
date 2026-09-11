# data-fetch delta

## ADDED Requirements

### Requirement: Backfill chunks wide `get_candles` requests

The backfill runner SHALL split every per-ticker fetch into 7-day
chunks along the requested `[from_, to]` window because the live
Tinkoff API rejects wider requests with `INVALID_ARGUMENT 30014`.

#### Scenario: One-year window is split into ~53 chunks

- **GIVEN** a `backfill_one` call with `from_=2024-01-01` and `to_=2024-12-31`
- **WHEN** the runner fetches candles for the figi
- **THEN** the runner SHALL call `get_candles` once per 7-day slice
  (53 calls total) and merge the results before writing to parquet
- **AND** the per-chunk call SHALL pass the slice's start/end dates
  directly without further aggregation

#### Scenario: A failing chunk does not abort the ticker

- **GIVEN** a chunk's `get_candles` raises any exception
- **WHEN** the runner processes that slice
- **THEN** the chunk SHALL be skipped with a `warn`-level log entry
- **AND** the remaining chunks SHALL still execute
- **AND** if every chunk failed the runner SHALL emit a single
  `ticker_progress` event with `status='error'` carrying the last
  chunk's error message

### Requirement: Backfill covers all instrument asset classes

The backfill runner SHALL iterate every figi present in the `instruments`
table regardless of its `class` (share, etf, bond, future), because the
live Tinkoff API serves `get_candles` for all of them.

#### Scenario: Bond figi is processed

- **GIVEN** `instruments` contains a row with `class='bond'` and a figi
- **WHEN** the runner reaches that figi
- **THEN** the chunked `get_candles` calls SHALL execute and a parquet
  file SHALL be written if any chunk returns bars

### Requirement: `last_bar_ts` is logged as ISO date for every ticker

Each `info`-level `bars_written=N` log entry SHALL carry a real
`last_bar_ts` ISO date rather than `None`, regardless of whether the
SDK returns candles as dataclass objects or pre-converted dicts.

#### Scenario: SDK returns candles as `{"ts": "YYYY-MM-DD"}` dicts

- **GIVEN** a candle list containing dicts with a `ts` field
- **WHEN** the runner derives `last_bar_ts`
- **THEN** the runner SHALL parse `c["ts"][:10]` via `date.fromisoformat`
- **AND** the resulting value SHALL be present in the `bars_written`
  log message

### Requirement: `PUT /api/settings/token` mirrors `last4` into settings

`PUT /api/settings/token` SHALL write the broker token to the
`secrets` table AND mirror `tokenLast4` (plus `tokenRedacted=true`)
into the structured `settings.value.broker` row, atomically per
operator action.

#### Scenario: First token write

- **GIVEN** no previous `settings.main` row exists
- **WHEN** the operator saves a token via `PUT /api/settings/token`
- **THEN** the handler SHALL insert a new `settings` row with
  `broker.tokenLast4 = token[-4:]` and `broker.tokenRedacted = true`

#### Scenario: Subsequent token write updates settings row version

- **GIVEN** a `settings.main` row already exists with `version='v1-xyz'`
- **WHEN** the operator saves a new token
- **THEN** the handler SHALL UPDATE the row with a fresh `version`
  and `broker.tokenLast4 = token[-4:]`
- **AND** the response body SHALL include the new `tokenLast4`

### Requirement: `set_secret` writes an audit row

Every call to `set_secret` SHALL append an entry to `ingestion_logs`
with `level='info'` and `message='secrets.write key=<key> last4=<XXXX>'`
so unexpected token overwrites are observable in the LogStrip.

#### Scenario: Token write appears in ingestion_logs

- **GIVEN** `set_secret('broker_token', 't.real.WXYZ')` is called
- **WHEN** the helper returns
- **THEN** an `ingestion_logs` row exists with `message` containing
  `secrets.write key=broker_token last4=WXYZ`
- **AND** the `figi` column on that row is the same `WXYZ` value
  (full token is never logged)

## REMOVED Requirements

### Requirement: `class IN ('share', 'etf')` filter in backfill universe

The runner SHALL NOT exclude bonds or futures from the candle loop;
this filter, briefly introduced during debugging, dropped thousands of
figis from the data-fetch pipeline.
