# Data quality Specification (delta)

## ADDED Requirements

### Requirement: Historical MOEX Evidence Validity Contract

The system SHALL classify every MOEX ISS `/iss/history/.../securities/{ticker}.json`
fetch performed by `apps.api.ingestion.backfill._fetch_year_moex`
into exactly one `MOEXFetchOutcome` value drawn from the set
`{complete, partial, error, malformed, identity_mismatch}`. The
function SHALL return the outcome alongside the list of parsed rows
it already returns today, so every caller can distinguish a
validated full paginated response from a degraded one.

Outcome reduction rules (the function MUST apply these verbatim):

1. `error` — any HTTP / network exception thrown while the loop is
   fetching pages.
2. `malformed` — response payload missing the `TRADEDATE` column,
   OR no `history.cursor` block AND short page (cannot certify
   pagination completeness on its own).
3. `partial` — cursor present but `offset + len(rows) < total`
   (pagination incomplete).
4. `identity_mismatch` — at least one parsed row carries
   `SECID != ticker` or `BOARDID != board`.
5. `complete` — every page parsed cleanly, every SECID / BOARDID
   matched the request, and the final cursor showed
   `offset + len(rows) >= total`.

The list of parsed rows returned by the function SHALL be unchanged
for bar consumers (existing partial-bar tolerance is preserved). The
outcome is the only addition.

#### Scenario: complete response with explicit zero-trade row

- GIVEN `backfill._fetch_year_moex("shares", "TQBR", "GAZP", 2025)`
  is called
- AND MOEX returns a full paginated response with a `history.cursor`
  whose final `offset + len(rows) == total`
- AND every emitted row has `SECID == "GAZP"` and `BOARDID == "TQBR"`
- WHEN the function returns
- THEN the outcome SHALL be `"complete"`
- AND the list SHALL contain every bar the upstream served (including
  the explicit zero-trade row with `OPEN=HIGH=LOW=CLOSE=None`,
  `VOLUME=NUMTRADES=VALUE=0`).

#### Scenario: network error is reported

- GIVEN `backfill._fetch_year_moex("bonds", "TQCB", "RU000A0JWVL2", 2024)`
- AND `requests.get` raises `ConnectionError` on the first HTTP call
- WHEN the function returns
- THEN the outcome SHALL be `"error"`
- AND the list SHALL be `[]`.

#### Scenario: malformed payload (no TRADEDATE column) is reported

- GIVEN a response with `history.columns = []`
- WHEN `backfill._fetch_year_moex` walks the response
- THEN the outcome SHALL be `"malformed"`
- AND the list SHALL be `[]`.

#### Scenario: short page with no cursor is reported

- GIVEN a response with `len(rows) < page_size` and no
  `history.cursor` block
- AND `page_size = 500` (the loop’s default)
- WHEN the function returns
- THEN the outcome SHALL be `"malformed"`
- (a short page on its own cannot certify that the upstream
  served the whole year).

#### Scenario: cursor shows incomplete pagination

- GIVEN the final cursor reports `offset + len(rows) < total`
- WHEN the function returns
- THEN the outcome SHALL be `"partial"`
- AND the list SHALL contain the rows parsed so far.

#### Scenario: SECID mismatch is reported

- GIVEN a response row carries `SECID = "SBER"` while the caller
  asked for `ticker = "GAZP"`
- WHEN the function returns
- THEN the outcome SHALL be `"identity_mismatch"`
- AND the list SHALL still contain every parsed row
- (bar consumers that already accepted the data unaffected.

### Requirement: Historical Evidence Walk for Stale Figis

The system SHALL record zero-trade evidence for a figi whose
`MAX(bars.ts)` is older than the last completed MOEX business day
only when:

1. The upstream `_fetch_year_moex` outcome for the historical window
   is `"complete"`;
2. Every emitted row carries `SECID == ticker` and
   `BOARDID == board`;
3. The figi’s `instruments.isin` (when non-empty) matches the
   upstream ISIN metadata fetched by
   `apps.api.ingestion.backfill._get_meta_moex`;
4. Every row’s `TRADEDATE` parses as a valid ISO date, falls inside
   the requested `[from_d, to_d]` window, AND is a business date
   (weekday AND not present in `moex_holidays`).

When ANY of conditions 1–4 fails for a figi, the helper SHALL
record no evidence rows for that figi and SHALL emit exactly one
structured log line:

    {event: "moex_historical_evidence_rejected",
     figi, ticker, reason, rows}

where `reason ∈ {partial, error, malformed, identity_mismatch,
non_business_date}`.

The bar list consumed by the backfill walker SHALL be unaffected:
when outcome is non-`complete`, the walker continues writing real
bars (existing partial-bar consumer behaviour) and only the
evidence path is skipped.

The writer lock contract from the `writer-coordination` capability
applies unchanged: the evidence write-section acquires the shared
lock once via the existing `_evidence_writer_lock` helper with
`role="no-trade-evidence"` and `phase="evidence"`. No new lock path,
no nested acquisition.

The new helper
`apps.api.ingestion.no_trade_evidence.record_historical_no_trade_evidence`
MUST delegate the actual `INSERT … ON CONFLICT` to the existing
`record_no_trade_evidence` so TTL semantics, recent-vs-historical
expiry branching, real-bar-wins filtering, and `ON CONFLICT` refresh
behaviour are preserved verbatim.

#### Scenario: complete historical response writes evidence for business dates only

- GIVEN a figi with `MAX(bars.ts)` = 2026-08-20
- AND `last_completed_session` = 2026-09-30
- AND `_fetch_year_moex("shares", "TQBR", "GAZP", 2026)` returns
  outcome `"complete"`
- AND the parsed rows include zero-trade rows for 2026-09-01
  (weekday, business), 2026-09-05 (Saturday, non-business),
  2026-09-06 (Sunday, non-business), and 2026-09-15 (weekday,
  holiday per `moex_holidays`)
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `moex_no_trade_evidence` SHALL contain exactly one row for
  the figi with `session_date = "2026-09-01"`
- AND no row SHALL be inserted for 2026-09-05, 2026-09-06, or
  2026-09-15.

#### Scenario: partial outcome is rejected

- GIVEN a figi whose historical fetch outcome is `"partial"`
- AND `_extract_zero_trade_rows` would have returned five rows
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `moex_no_trade_evidence` SHALL remain unchanged for that
  figi
- AND one structured log line with
  `{event: "moex_historical_evidence_rejected", reason: "partial"}`
  SHALL be emitted
- AND the bars list passed to the backfill writer SHALL be
  unaffected (the walker writes whatever real bars the partial
  response had, preserving existing partial-bar tolerance).

#### Scenario: identity_mismatch outcome is rejected

- GIVEN a figi whose historical fetch outcome is `"identity_mismatch"`
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `moex_no_trade_evidence` SHALL remain unchanged for that
  figi
- AND one structured log line with
  `{event: "moex_historical_evidence_rejected",
  reason: "identity_mismatch"}` SHALL be emitted.

#### Scenario: rows on non-business dates are skipped

- GIVEN a figi whose historical fetch outcome is `"complete"`
- AND the parsed rows include zero-trade rows for 2026-10-03
  (Saturday) and 2026-10-04 (Sunday) in addition to a weekday
  zero-trade row
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `moex_no_trade_evidence` SHALL contain a row for the weekday
  date only
- AND one structured log line with
  `{event: "moex_historical_evidence_rejected",
  reason: "non_business_date", rows: 2}` SHALL be emitted for the
  weekend pair.

#### Scenario: helper preserves TTL semantics

- GIVEN a figi whose historical fetch outcome is `"complete"`
- AND the accepted business-date rows include one row whose date
  falls within the last 14 calendar days (recent) and one row whose
  date is older (historical)
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `expires_at` for the recent row SHALL equal
  `today + RECENT_EVIDENCE_EXPIRY` (7 days)
- AND `expires_at` for the historical row SHALL equal
  `today + HISTORICAL_EVIDENCE_EXPIRY` (365 days).

#### Scenario: real bar wins

- GIVEN a figi with an existing bar for 2026-09-15
- AND a complete historical response that includes an explicit
  zero-trade row for the same date
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN no `moex_no_trade_evidence` row SHALL be inserted for
  2026-09-15
- AND the existing bar in `bars` SHALL be unchanged.

#### Scenario: writer lock is acquired once per write-section

- GIVEN two processes target the same file-backed SQLite DB
- AND process A holds the shared writer lock
- WHEN process B calls
  `record_historical_no_trade_evidence(..., db_path=db_path)` and
  the shared lock is busy
- THEN `WriterLockBusy` SHALL propagate to the caller
- AND no SQLite mutation SHALL occur in process B
- AND the existing `record_no_trade_evidence` semantics SHALL
  remain unchanged.

#### Scenario: CLI exits 0 with zero evidence rows on partial outcome

- GIVEN the historical CLI is invoked against a file-backed temp DB
- AND a stubbed `_fetch_year_moex` returns `outcome = "partial"`
- WHEN `python apps/api/scripts/backfill_no_trade_evidence.py`
  runs against the test figi
- THEN the CLI SHALL exit `0`
- AND `moex_no_trade_evidence` SHALL have zero rows for that figi
- AND stdout SHALL contain exactly one
  `moex_historical_evidence_rejected` line for that figi with
  `reason: "partial"`.