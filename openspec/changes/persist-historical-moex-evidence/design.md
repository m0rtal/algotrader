# Design: Persist validated historical MOEX zero-trade evidence

## Stack

- Python 3.11 (no new dependencies; `sqlite3`, `urllib.parse`, `requests`
  only; everything else is stdlib).
- SQLite WAL, existing `moex_no_trade_evidence` schema (no migration).
- MOEX ISS endpoint
  `https://iss.moex.com/iss/history/engines/stock/markets/{market}/boards/{board}/securities/{ticker}.json`
  with paginated cursor and SECID / BOARDID columns (already consumed).
- Existing shared writer lock helper
  `apps.api.ingestion.writer_lock.writer_lock` (acquired exactly once
  per evidence write-section; no nested acquisitions; no lock on
  network or computation).
- Existing helper
  `apps.api.ingestion.no_trade_evidence.record_no_trade_evidence`
  (TTL / recent-vs-historical expiry / real-bar-wins / ON CONFLICT
  refresh; reused verbatim).

## Layout of files

| File | Concern |
|---|---|
| `apps/api/src/algotrader_api/ingestion/backfill.py` | New `_fetch_year_moex_outcome` returns `MOEXFetchOutcome` alongside `list[dict]`; the loop assigns it per page and reduces it via a small `_reduce_outcomes` helper that records the worst severity (error > malformed > partial > identity_mismatch > complete). The legacy `_fetch_year_moex(market, board, ticker, year, last_trading_day=None)` signature is preserved (positional-or-keyword, NOT keyword-only) and is a thin wrapper around the new iterator. `BackfillRunner._process_moex_year` and `_process_one` (historical walk branch) consume the outcome and route only `complete` outcomes to the evidence helper. |
| `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py` | New `record_historical_no_trade_evidence` helper that gates on outcome + business-date filter; new `MOEXFetchOutcome` `Literal` declared here and re-exported by `backfill.py` via a module-level alias (no re-declaration, no circular import). Business-date filter helper `_is_business_date_for_evidence` (weekend + `moex_holidays`) lives next to it. Existing `record_no_trade_evidence` and `_record_no_trade_evidence_tx` unchanged. |
| `apps/api/scripts/backfill_no_trade_evidence.py` | Thin call-through: replace the existing `record_no_trade_evidence(...)` call with `record_historical_no_trade_evidence(..., outcome=outcome, ticker=ticker)`. The CLI does not gain a new branch — it picks up the outcome gate, the business-date filter, and the structured `moex_historical_evidence_rejected` line by routing through the new helper. No new argument surface; no new exit code path. |
| `apps/api/tests/test_moex_no_trade_evidence.py` | New RED/GREEN tests for outcome classification, business-date filter, ISIN-mismatch batch poisoning, helper signature (explicit `ticker` arg), and bar-list independence. The test doubles MUST set `FakeResp.status_code = 200` and provide row lists whose length matches the declared columns. Plain-list mocks (`mock = [...]` without status / cursor) cannot certify a complete response. |
| `apps/api/tests/test_backfill_source_routing.py` | New RED/GREEN tests for `BackfillRunner._process_moex_year` and `_process_one` historical walk branch — partial / error / malformed / identity_mismatch outcomes leave `moex_no_trade_evidence` untouched; the bar list passed to `replace_bars_for_figi` is unchanged for those outcomes. The walker is bound to the new outcome-emitting fetch (not a plain list) via `monkeypatch.setattr(runner, "_fetch_year_moex_outcome", ...)`. |
| `apps/api/tests/test_backfill_no_trade_evidence_lock.py` | New tests: CLI returns `0 rows` and exits `0` on partial / error / malformed / identity_mismatch outcomes; writes no evidence rows; emits the structured log key. Tests run against a file-backed temp DB so the kernel-level `flock` is exercised. |
| `apps/api/tests/test_moex_all_paths_identity.py` | Regression: `_fetch_year_moex_outcome` carries the outcome; SECID / BOARDID disagreement returns `identity_mismatch`; bar list is unchanged. |
| `apps/api/tests/test_backfill_coverage.py` | New end-to-end test: real public walker consumes a strict synthetic feed (HTTP 200, valid row lengths, explicit zero counters, no missing OHLC, no identity mismatch), writes only explicit zero-trade evidence (no fake OHLC bars), `populate_expected_bars` recomputes the canonical `expected_bars` on the TEMP DB, and `check_coverage([figi])` returns `ok=True`. The test asserts the pre-evidence ratio is below 0.95 and the post-evidence ratio is at or above 0.95 — no manual denominator override. Tests never read production / never call the live broker. |

## Data Flow

```
       ┌─────────────────────────────────────────────┐
       │  backfill._fetch_year_moex(market, board,   │
       │                       ticker, year)         │
       │   ┌───────────────────────────────────┐     │
       │   │  Loop: GET /iss/history/...?      │     │
       │   │   parse cols + rows + cursor      │     │
       │   │   reduce outcome per page         │     │
       │   └───────────────────────────────────┘     │
       │   return (rows: list[dict],                 │
       │           outcome: MOEXFetchOutcome)        │
       └──────────────┬──────────────────────────────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
        ▼                           ▼
  bar consumer                 evidence consumer
  (existing callers;            (record_historical_no_trade_evidence)
   unchanged)
   - replace_bars_for_figi        gate on outcome == "complete"
   - backfill_no_trade_evidence  filter on business-date
                                 (weekend / holiday / fromisoformat)
                                 identity check on rows (SECID/BOARDID)
                                 delegate to record_no_trade_evidence
                                 (TTL, expiry, ON CONFLICT, real-bar-wins)
                                 acquire writer_lock once
                                 (`role="no-trade-evidence"`,
                                 `phase="evidence"`)
```

The bar consumer keeps the existing list; the evidence consumer
branches on outcome so a degraded fetch can never be promoted into
durable evidence.

## Outcome reduction rules

`_fetch_year_moex` walks one year and may issue multiple HTTP
requests. The outcome returned to the caller is the *worst* severity
seen, in this order:

1. `error` — any HTTP / network exception thrown inside the loop.
2. `malformed` — response payload without `TRADEDATE` column OR
   without `history.cursor` AND short page (cannot certify pagination
   completeness on its own) OR `status_code != 200` OR the requested
   `start` cursor offset does not match the server-reported cursor
   offset OR the cursor's reported `total` is non-positive OR a
   repeated cursor offset is observed (loop did not advance).
3. `partial` — cursor present and consistent, but
   `offset + len(rows) < total` (pagination incomplete on the final
   page).
4. `identity_mismatch` — at least one row carries
   `SECID != ticker` or `BOARDID != board` (this is a per-row
   check; it is reported at the call level when at least one row
   mismatches; a single cross-listed mirror row poisons the entire
   batch — identity is per-batch).
5. `complete` — all pages parsed cleanly, all SECID / BOARDID
   matched the request, the final cursor showed
   `offset + len(rows) >= total`, the loop advanced monotonically,
   and every row passed the strict feed contract (HTTP 200, row
   length matches the column-list length, OHLC + VOLUME present,
   zero-trade rows carry explicit `NUMTRADES == 0` and
   `VALUE == 0`).

`MOEXFetchOutcome` is intentionally a `Literal` alias (not an Enum
and not a runtime constructor). Callers MUST compare the outcome
with a plain string literal (`outcome == "complete"`); the alias
exists only to give the function signature a precise return type
that a static type-checker can verify. The alias is declared in
`apps.api.ingestion.no_trade_evidence` and re-exported by
`apps.api.ingestion.backfill` via a `TYPE_CHECKING` guarded import
or a module-level alias, so callers always import the same symbol
and there is no circular import between the two modules.

The strict feed contract (row-level validation) is what the
fetcher uses to *drop* non-conforming rows before they reach the
bar consumer, so the bar consumer sees a clean list and the
evidence consumer never sees a row that fails the explicit
zero-trade shape. The list returned by the function is the rows
that survived the strict feed contract; the outcome is the verdict
on the entire fetch.

## Lock contract

`_evidence_writer_lock(db_path, role="no-trade-evidence",
phase="evidence")` is the single audit point already exposed by
`no_trade_evidence.py`. The new helper calls it once around its
`_record_no_trade_evidence_tx` call. The private transaction body
remains the existing `_record_no_trade_evidence_tx` — it is not
modified. No new lock path; no new role / phase; no nested
acquisition. Listed-till UPDATE (separate write-section, separate
acquisition) is unchanged.

## Edge cases and risks

- An empty upstream body (`{"data": []}` and short page) must NOT
  silently be classified as `complete`. It is `malformed` because
  we cannot certify that the upstream intended “no rows” versus
  “failure to render”. The same rule applies to a `len(rows) <
  page_size` first page with no cursor: a single short first page
  with no cursor is `malformed` regardless of its row count.
- A partial response can include rows for the requested dates and
  rows for past dates that MOEX spilled in. The existing
  `_fetch_moex_range` post-filter keeps only `[from_d, to_d]` rows;
  the evidence helper now applies the same filter via the
  business-date check.
- The strict feed contract rejects non-conforming rows at fetch
  time. The fetcher MUST check `response.status_code == 200` and
  drop rows whose length does not match the column-list length.
  The `FakeResp` test double in the new tests MUST set
  `status_code = 200` and provide a row list whose length matches
  the declared columns; a missing `status_code` is `malformed`, a
  short row is dropped (or, for the zero-trade shape, `malformed`).
  Tests that lower validation to fit a sloppy mock are
  unacceptable — the mock must be tightened, not the production
  code weakened.
- A non-positive cursor `total` (e.g. `-1` or `0` with no rows
  served) is `malformed` — we cannot certify completeness on a
  total we cannot reason about.
- A repeated cursor offset across two pages is `malformed` (the
  loop must advance; a no-progress loop is a server-side bug we
  refuse to certify).
- The bar consumer keeps the existing list contract: rows that
  pass the strict feed contract on a non-final page are appended;
  the bar consumer sees the same list regardless of outcome, so
  existing partial-bar tests keep passing.
- A plain list `mock = [...]` (without a status code or a cursor)
  cannot certify a complete response. The fetcher treats a
  missing status code as `malformed`; tests that rely on plain
  list mocks for the evidence path are tests of the bar consumer,
  not the evidence contract.
- The `record_historical_no_trade_evidence` helper signature
  carries the explicit `ticker` argument. The helper MUST NOT
  guess the ticker from `rows[0].get("_secid")` — the caller
  (the historical walker) threads the ticker through from the
  `instruments` row, so the identity check is end-to-end.
  ISIN cross-check (from `_get_meta_moex`) is performed at the
  caller level (in the historical walker) and is a
  batch-poisoning check: a single ISIN mismatch rejects the
  whole batch.
- Tests must remain `:memory:`-friendly where they don’t assert
  interprocess locking. The new outcome / business-date /
  identity-mismatch tests run against the private SQL helper and
  must not require the writer lock. The CLI outcome-gating test
  DOES need a file-backed temp DB so the kernel-level `flock` is
  exercised for the existing evidence wrapper.