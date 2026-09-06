# backend-api Specification (delta)

## ADDED Requirements

### Requirement: Read-Only API Surface

The system SHALL expose a JSON HTTP API at `/api/*` whose response shapes
match exactly the Zod schemas defined in `@algotrader/shared`. Frontend
code SHALL be able to switch from MSW mocks to the real backend by
changing `VITE_API_BASE_URL` with no other code changes.

#### Scenario: Frontend swap MSW ↔ backend

- GIVEN the frontend is configured with `VITE_API_BASE_URL=http://localhost:8000/api`
- AND the backend service is running
- WHEN the dashboard requests `/api/kpis`
- THEN the response validates against `KpiSchema` from `@algotrader/shared`
- AND the UI renders the same way it does against MSW

#### Scenario: Unknown ticker returns 404

- GIVEN no bars file exists for ticker `UNKNOWN`
- WHEN a client requests `GET /api/bars/UNKNOWN`
- THEN the service returns HTTP 404 with JSON `{"error": "ticker_not_found"}`

### Requirement: Settings Persistence

The system SHALL persist user settings (broker token, risk limits, ML
config, data source) to SQLite. Reads SHALL return defaults from
`@algotrader/shared` when no row exists. Writes SHALL validate against
`SettingsSchema` and enforce optimistic concurrency via a version hash.

#### Scenario: Default settings returned on first read

- GIVEN the settings table is empty
- WHEN a client requests `GET /api/settings`
- THEN the service returns HTTP 200 with `{values: DEFAULT_SETTINGS, version: "", updatedAt: <now>}`

#### Scenario: Successful save updates baseline

- GIVEN the client has loaded settings with version `v1-abc`
- WHEN the client sends `PUT /api/settings` with `{values, version: "v1-abc"}`
- THEN the service stores the new values with a fresh version hash
- AND returns HTTP 200 with `{values, version: <new hash>, updatedAt: <now>}`

#### Scenario: Stale version returns 409

- GIVEN the settings row has version `v1-abc`
- WHEN the client sends `PUT /api/settings` with `{values, version: "v1-xyz"}`
- THEN the service returns HTTP 409 with `{error: "version_conflict", current: <stored row>}`

#### Scenario: Reset returns to defaults

- GIVEN settings exist in the database
- WHEN the client sends `DELETE /api/settings`
- THEN the service drops the row and returns HTTP 204
- AND a subsequent `GET /api/settings` returns defaults

### Requirement: Bars Storage

The system SHALL store daily OHLCV bars as Parquet files at
`data/bars/<ticker>.parquet`. Each file SHALL contain columns
`ts DATE, open DECIMAL, high DECIMAL, low DECIMAL, close DECIMAL,
volume BIGINT, adj_close DECIMAL` compressed with zstd. Reads SHALL go
through DuckDB.

#### Scenario: Query single ticker

- GIVEN bars exist for `SBER` covering 2024-01-01..2024-12-31
- WHEN a client requests `GET /api/bars/SBER?from=2024-06-01&till=2024-08-31`
- THEN the service returns bars for the date range only
- AND the response validates against `BarsSeriesSchema`

#### Scenario: Filter by date range

- GIVEN bars exist for `SBER` covering 5 years
- WHEN a client requests bars without `from`/`till` query params
- THEN the service returns all 252 bars per year for that ticker

### Requirement: Backend Health Endpoint

The system SHALL expose `GET /health` returning HTTP 200 with
`{status: "ok", sqlite: "ok", duckdb: "ok", bars_count: <int>}`. The
endpoint SHALL verify SQLite and DuckDB connectivity on each call.

#### Scenario: Service ready

- GIVEN the backend is running with seeded data
- WHEN a client requests `GET /health`
- THEN the service returns 200 with `bars_count > 0`

#### Scenario: DuckDB down

- GIVEN DuckDB connection fails (e.g., disk full)
- WHEN a client requests `GET /health`
- THEN the service returns 503 with `{status: "degraded", duckdb: "error"}`

### Requirement: LAN-Bind by Default

The system SHALL default to binding only `127.0.0.1` in production.
Wide-area bind (`0.0.0.0`) is opt-in via env var. CORS SHALL be
restricted to a configurable whitelist of frontend origins.

#### Scenario: Default config binds localhost

- GIVEN `ALGOTRADER_API_HOST` is unset
- WHEN the service starts
- THEN it binds to `127.0.0.1` and refuses connections from non-loopback IPs

#### Scenario: CORS rejects unknown origin

- GIVEN `ALGOTRADER_CORS_ORIGINS=http://localhost:5173`
- WHEN a browser sends a preflight from `https://evil.com`
- THEN CORS middleware denies the request

### Requirement: Structured Logging

The system SHALL log all requests, errors, and state changes via
`structlog` in JSON format. Logs SHALL NOT contain secrets (token,
passwords, account IDs in URL).

#### Scenario: Settings PUT does not log token

- GIVEN the client sends `PUT /api/settings` with tokenLast4=`ABCD`
- WHEN the service logs the request
- THEN the log entry contains `event="settings.put"` and no full token value

### Requirement: Test Coverage ≥95%

The system SHALL enforce ≥95% coverage on lines, branches, functions,
and statements in `pytest --cov`. Coverage threshold failures SHALL
fail CI.

#### Scenario: Coverage gate enforced

- GIVEN the test suite runs with coverage report
- WHEN any metric drops below 95%
- THEN pytest exits with non-zero status

### Requirement: Distributed Tracing via OpenTelemetry

The system SHALL emit OpenTelemetry traces for every HTTP request, DB
query, and manual business event. Traces SHALL be exported via OTLP gRPC
to a collector (default `http://localhost:4317`). Each trace SHALL
include `trace_id` and `span_id` as attributes on every log event so
operators can pivot from a log line to the full request waterfall.

#### Scenario: HTTP request creates root span

- GIVEN the service is running
- WHEN a client sends `GET /api/kpis`
- THEN the service emits a span `HTTP /api/kpis` with attributes
      `http.method=GET`, `http.route=/api/kpis`, `http.status_code=200`,
      and `http.duration_ms=<int>`

#### Scenario: DB query wrapped in child span

- GIVEN a request handler calls `db.execute("SELECT ...")`
- WHEN the query runs
- THEN the service emits a child span `db.query.sqlite` with attributes
      `db.statement=<truncated SQL>`, `db.system=sqlite`, `db.duration_ms`,
      `db.row_count`

#### Scenario: Manual business span for settings mutation

- GIVEN the client sends `PUT /api/settings`
- WHEN the service processes the request
- THEN the service emits a span `settings.put` with attributes
      `settings.version_old`, `settings.version_new`, `settings.section`

### Requirement: Correlation ID Propagation

The system SHALL generate or read a correlation ID per request, expose it
as `X-Correlation-ID` response header, store it in a context variable
accessible from any code path within the request, and include it as
`correlation_id` attribute on every log event and span emitted during
that request.

#### Scenario: Correlation ID generated when missing

- GIVEN a client sends `GET /api/kpis` without `X-Correlation-ID` header
- WHEN the service processes the request
- THEN the response includes `X-Correlation-ID: <uuid>` header
- AND the generated ID appears in all log events for this request

#### Scenario: Correlation ID propagated when present

- GIVEN a client sends `GET /api/kpis` with header `X-Correlation-ID: trace-abc`
- WHEN the service processes the request
- THEN the response echoes `X-Correlation-ID: trace-abc`
- AND log events for this request carry `correlation_id="trace-abc"`

### Requirement: Secret and PII Scrubbing

The system SHALL redact fields whose names match `token`, `tokenLast4`,
`password`, `secret`, or `authorization` to `[REDACTED]` before any log
event leaves the process. Account IDs SHALL be masked to first 2 + last
2 characters with `***` between.

#### Scenario: Token never reaches log output

- GIVEN the client sends `PUT /api/settings` with `broker.tokenLast4="ABCD"`
- WHEN the service logs the request body or the structured attributes
- THEN the log output contains `[REDACTED]` (or similar) and never the
      literal `ABCD`

#### Scenario: Account ID partially masked

- GIVEN a log event contains `accountId="ACC-123456789"`
- WHEN the scrubber processor runs
- THEN the log output shows `accountId="AC***89"`

### Requirement: Log Sampling for High-Frequency Endpoints

The system SHALL sample health check log events at the rate defined by
`ALGOTRADER_LOG_SAMPLE_HEALTH` (default 0.1, i.e. 10%). Error responses
(HTTP ≥500, HTTP 4xx except 401/403/404) SHALL bypass sampling and be
logged in full.

#### Scenario: Health check sampled

- GIVEN `ALGOTRADER_LOG_SAMPLE_HEALTH=0.1`
- WHEN the service receives 100 health check requests
- THEN approximately 10 log events are emitted (binomial distribution)

#### Scenario: Error responses never sampled

- GIVEN a request to `/api/unknown` returns HTTP 404
- WHEN the error response is sent
- THEN the log event is always emitted, regardless of sample rate

### Requirement: Collector Outage Degraded Mode

The system SHALL fall back to stdout JSON logging if the OTel collector
is unreachable for more than 5 seconds. A single warning event SHALL be
emitted on first failure to avoid log spam.

#### Scenario: Collector down

- GIVEN the OTel collector endpoint is unreachable
- WHEN the service attempts to export logs and traces
- THEN a single warning event `observability.collector.down` is emitted
- AND subsequent log events continue to stdout JSON
- AND on collector recovery, export resumes without service restart

## REMOVED Requirements

### Requirement: MSW as Production Backend

The system SHALL NOT depend on MSW in production. MSW remains available
as a development fallback when `VITE_API_BASE_URL=/api` and the backend
is unreachable, but production deployments MUST point at the real
backend.

#### Scenario: Production points at real backend

- GIVEN the frontend is built for production
- WHEN the bundle is served
- THEN `VITE_API_BASE_URL` points at `http://localhost:8000/api` (or the
      configured backend URL), not at MSW
