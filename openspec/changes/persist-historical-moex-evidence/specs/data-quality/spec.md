# Data quality Specification (delta)

## ADDED Requirements

### Requirement: Historical MOEX Evidence Validity Contract

The system SHALL classify every MOEX ISS `/iss/history/.../securities/{ticker}.json`
fetch performed by `apps.api.ingestion.backfill._fetch_year_moex`
into exactly one `MOEXFetchOutcome` value drawn from the set
`{complete, partial, error, malformed, identity_mismatch}`. The
outcome is a `typing.Literal` alias declared in
`apps.api.ingestion.no_trade_evidence` and re-exported (NOT
re-declared) by `apps.api.ingestion.backfill` so callers always
import the same symbol. The function SHALL return the outcome
alongside the list of parsed rows it already returns today, so every
caller can distinguish a validated full paginated response from a
degraded one. Callers compare the outcome with a plain string
literal (`outcome == "complete"`, never `MOEXFetchOutcome("complete")`
— the alias is not a runtime constructor).

Outcome reduction rules (the function MUST apply these verbatim):

1. `error` — any HTTP / network exception thrown while the loop is
   fetching pages.
2. `malformed` — response payload missing the `TRADEDATE` column,
   OR no `history.cursor` block AND short page (cannot certify
   pagination completeness on its own), OR HTTP `status_code != 200`,
   OR the requested `start` cursor offset does not match the
   server-reported cursor offset, OR the cursor's reported page
   size does not match the page size the loop actually received
   (consistency check), OR a repeated cursor offset is observed
   (the loop must advance, never loop), OR the cursor's `total` is
   non-positive.
3. `partial` — cursor present and consistent, but
   `offset + len(rows) < total` (pagination incomplete on the final
   page).
4. `identity_mismatch` — at least one parsed row carries
   `SECID != ticker` or `BOARDID != board` (single cross-listed
   mirror row poisons the entire batch — identity is per-batch).
5. `complete` — every page parsed cleanly, every SECID / BOARDID
   matched the request, the final cursor showed
   `offset + len(rows) >= total`, the loop advanced monotonically,
   and the empty final-page short-circuit fired only after a full
   page of rows preceded it (a `len(rows) < page_size` first page
   with no cursor cannot certify completeness).

Strict feed contract (each row the loop accepts as input to
`complete` MUST satisfy all of these; the fetcher drops non-conforming
rows before the row list is returned, so the bar consumer sees
cleaned data and the evidence consumer never sees non-conforming
rows):

- HTTP `status_code == 200` for the page.
- Row length equals the column-list length (no malformed rows).
- The four OHLC columns are present (and parsed as `None` for the
  explicit zero-trade shape, not as missing/missing-converted-None);
  missing OHLC on a non-zero `VOLUME` row is malformed.
- `VOLUME` is present (the column itself; zero is a valid value).
- `NUMTRADES` and `VALUE` are present and equal to `0` for the
  zero-trade shape; missing counters on a zero-trade row are
  malformed (the helper relies on the explicit `0`).

The list of parsed rows returned by the function SHALL be unchanged
for bar consumers (existing partial-bar tolerance is preserved for
rows that already passed the strict feed contract on a non-final
page; the bar consumer keeps its current list with no behaviour
change). The outcome is the only addition.

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
  (bar consumers that already accepted the data unaffected).

#### Scenario: non-200 HTTP status is reported as malformed

- GIVEN `requests.get` returns a response with
  `status_code = 500`
- AND the body would otherwise parse cleanly
- WHEN the function returns
- THEN the outcome SHALL be `"malformed"`
- AND the list SHALL be `[]`.

#### Scenario: missing OHLC on a non-zero volume row is reported as malformed

- GIVEN a response row carries `OPEN=HIGH=LOW=CLOSE` absent AND
  `VOLUME > 0`
- WHEN the function returns
- THEN the outcome SHALL be `"malformed"`
- AND the offending row SHALL be dropped from the list (bar
  consumer sees a clean list).

#### Scenario: initial cursor offset mismatch is reported as malformed

- GIVEN the loop requests `start=0` AND the server-reported
  `history.cursor` offset is `2`
- WHEN the function returns
- THEN the outcome SHALL be `"malformed"`
- (the server told us a different starting point than we asked for;
  we cannot certify completeness on a cursor we did not start
  from).

#### Scenario: repeated cursor offset is reported as malformed

- GIVEN two consecutive pages return the same `history.cursor`
  offset (server-side loop / no progress)
- WHEN the function returns
- THEN the outcome SHALL be `"malformed"`
- AND the row list SHALL be the union of the two pages' rows
  (no bar consumer data loss beyond what the bad response
  itself produced).

#### Scenario: identity_mismatch poisons the whole batch

- GIVEN a complete paginated response that contains one row with
  `SECID = "SBER"` while the caller asked for `ticker = "GAZP"`
- AND every other row matches identity
- WHEN the function returns
- THEN the outcome SHALL be `"identity_mismatch"` (a single
  cross-listed mirror row is enough; identity is per-batch).

### Requirement: Historical Evidence Walk for Stale Figis

The system SHALL record zero-trade evidence for a figi whose
`MAX(bars.ts)` is older than the last completed MOEX business day
only when:

1. The upstream `_fetch_year_moex` outcome for the historical window
   is `"complete"`;
2. Every emitted row carries `SECID == ticker` AND
   `BOARDID == board` (the helper is invoked with the explicit
   `ticker` carried from the producer through the caller — the
   helper MUST NOT guess the ticker from `rows[0].get("_secid")`);
3. The figi’s `instruments.isin` (when non-empty) matches the
   upstream ISIN metadata fetched by
   `apps.api.ingestion.backfill._get_meta_moex`; a single ISIN
   mismatch is a batch-poisoning event (the helper rejects the
   whole batch even if the per-row SECID/BOARDID checks passed);
4. Every row’s `TRADEDATE` parses as a valid ISO date, falls inside
   the requested `[from_d, to_d]` window, AND is a business date
   (weekday AND not present in `moex_holidays`).

The helper signature is
`record_historical_no_trade_evidence(conn, *, db_path, figi, ticker,
rows, board, isin, outcome, from_d, to_d, today=None) -> int`. The explicit
`ticker` parameter lets the helper perform the per-row identity filter without
guessing from `rows[0]`. Mandatory `from_d` and `to_d` are ordered date objects
carrying the actual caller window. Invalid or omitted windows MUST NOT certify
any evidence. The public walker passes its listing/requested window intersected
with each fetched year; the per-ticker walker passes its requested range; the
CLI passes its chosen `[lo, win_hi]` without adding CLI arguments.

The helper SHALL independently require normalized `open`, `high`, `low`, `close`
keys all explicitly present and `None`, and `volume`, `_numtrades`, `_value`
explicitly finite numeric zero (not bool, missing, None, or strings). Positive
trade rows SHALL be skipped, never inferred as evidence from missing local bars.
Their presence SHALL NOT poison valid zero rows in a validated complete feed.
Dates SHALL be exact `YYYY-MM-DD`, within the caller window, strictly before
`today` (or the current date), weekdays, and absent from cached `moex_holidays`.
Thus evidence cannot exceed the last completed published business session.

Malformed JSON containers, missing required structural blocks, invalid numeric
rows or cursor containers SHALL return `malformed`, not raise an unchecked
container exception. Valid bars collected on preceding pages SHALL remain in
the returned list. No duplicated HTTP fetch is permitted.

Non-complete outcomes and ISIN mismatch SHALL reject the whole batch with one
structured log line. Individual invalid dates, identities and non-zero shapes
SHALL be skipped; valid explicit zeros may still be recorded. Out-of-window
rows SHALL emit one bounded batch diagnostic, even when other rows are accepted.
An empty eligible set SHALL produce no evidence. Diagnostics use:

    {event: "moex_historical_evidence_rejected",
     figi, reason, rows}

where `reason ∈ {partial, error, malformed, identity_mismatch,
non_business_date, out_of_window, no_eligible_zero_session}`.

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

#### Scenario: positive missing bar does not prove no trade

- GIVEN a validated complete feed containing a null-OHLC zero-counter row for
  2026-09-01 and a positive-trade OHLC row for 2026-09-02
- AND neither session has a local bar
- WHEN the historical CLI passes the same fetched rows to the shared helper
  with caller window `[2026-09-01, 2026-09-30]`
- THEN only 2026-09-01 SHALL be recorded as evidence
- AND `bars` SHALL remain unchanged
- AND missing OHLC keys, missing counters, bool counters, fractional volume,
  NaN or negative volume SHALL never qualify as explicit zero evidence.

#### Scenario: caller window and completed-session cap cannot be widened by year fetch

- GIVEN `today = 2026-10-02` and caller window `[2026-09-01, 2026-09-30]`
- AND the complete annual feed also returns explicit zeros for 2026-01-05,
  2026-10-02, 2026-10-05 and 2099-01-05
- WHEN the shared helper evaluates these rows
- THEN none of those four dates SHALL be evidence
- AND a valid 2026-09-01 zero row SHALL be accepted
- AND out-of-window rows SHALL produce one bounded rejection diagnostic
- AND non-ISO suffixes SHALL not be truncated into accepted dates.

#### Scenario: malformed JSON after a valid first page preserves partial bars

- GIVEN a valid positive bar on the first HTTP 200 page and a cursor promising
  a second page
- AND the second JSON response is null, a list, a null history block, malformed
  columns, malformed raw rows or a malformed cursor container
- WHEN `_fetch_year_moex_outcome` walks the finite response sequence
- THEN it SHALL return `malformed` without an unchecked container exception
- AND its returned rows SHALL retain the first positive bar
- AND the evidence path SHALL record nothing from the malformed batch.

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

#### Scenario: ISIN mismatch poisons the whole batch

- GIVEN a figi whose `instruments.isin` is `"RU000A107UL4"` AND
  `_get_meta_moex` returns `isin = "US00206R1023"` for the
  upstream board
- AND the per-row SECID/BOARDID checks all pass
- WHEN `record_historical_no_trade_evidence` runs for that figi
- THEN `moex_no_trade_evidence` SHALL remain unchanged for that
  figi
- AND one structured log line with
  `{event: "moex_historical_evidence_rejected",
  reason: "identity_mismatch"}` SHALL be emitted (a single ISIN
  mismatch is a batch-poisoning event; the helper does not
  attempt to record partial-batch evidence).

#### Scenario: helper signature carries explicit ticker

- GIVEN `record_historical_no_trade_evidence` is called with
  `ticker = "GAZP"` AND `rows` whose first row has
  `_secid = "SBER"`
- WHEN the helper runs
- THEN it MUST compare every row's `_secid` against the
  explicit `ticker` parameter (not against `rows[0].get("_secid")`)
- AND the batch SHALL be rejected for `identity_mismatch`.

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

#### Scenario: end-to-end walker writes only explicit zero-trade evidence

- GIVEN a figi instrument seeded on a TEMP DB with
  `instruments.expected_bars` set to `expected_business_days(2024-01-01, 2024-12-31)`
  AND a real `bars` row only for `2024-01-15`
- AND `_fetch_year_moex` returns a strict synthetic feed:
  one page, `start=0`/`offset=0`/`total=N`/`page_size=N`,
  `status_code = 200`, every row has matching SECID/BOARDID,
  every row that is not `2024-01-15` is an explicit zero-trade
  shape (`OPEN=HIGH=LOW=CLOSE=None`, `VOLUME=NUMTRADES=VALUE=0`)
  on a weekday not in `moex_holidays`, every row's `ts` is a
  valid ISO date inside `[2024-01-01, 2024-12-31]`
- WHEN the historical walker
  (`BackfillRunner._process_moex_year` and the in-process call
  path) runs for that figi
- AND `populate_expected_bars` recomputes the canonical
  `expected_bars` for the figi (TEMP DB only — the production
  threshold / universe / expected formula are unchanged)
- AND `check_coverage([figi])` is called
- THEN `bars` SHALL contain exactly one row (the `2024-01-15`
  real bar) — no fabricated OHLC rows
- AND `moex_no_trade_evidence` SHALL contain one row per
  weekday, non-holiday, in-window zero-trade date the upstream
  served (TTL semantics preserved: recent → 7 days, historical
  → 365 days)
- AND `check_coverage([figi])` SHALL return `ok=True`
- AND the figure's `bars_count / expected_bars` ratio SHALL
  be `>= 0.95` (the canonical threshold; no manual denominator
  override)
- AND without the evidence write, the same synthetic feed
  would have left `check_coverage([figi])` failing
  (the test asserts the pre-evidence ratio is below `0.95`,
  the post-evidence ratio is at or above it).