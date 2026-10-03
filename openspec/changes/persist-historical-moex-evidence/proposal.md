# Proposal: Persist validated historical MOEX zero-trade evidence

## Why

The recent-tail pass and the historical walk already share an evidence
helper (`record_no_trade_evidence` + `_extract_zero_trade_rows`) that
writes per-figi no-trade confirmations into `moex_no_trade_evidence`.
Recent-tail works because the per-day fetch window is small and the
implicit “list of rows = full response” assumption holds.

The historical walk cannot use the same assumption. The shared fetcher
`apps.api.ingestion.backfill._fetch_year_moex` walks MOEX ISS
`/iss/history/.../securities/{ticker}.json` one calendar year at a
time, one `history.cursor`-paged chunk per HTTP request. The function
already threads `_secid` / `_boardid` / `_numtrades` / `_value` onto
every emitted dict, and the helper already requires those fields to
build evidence. But the helper cannot tell whether the upstream
response was a *complete paginated* response or an *error / partial /
malformed* one — when MOEX returns a network exception, an empty body,
a missing `history.cursor`, or an incomplete cursor the fetcher
silently returns the rows it had so far. There is no evidence-state
provenance today: the bar list and the evidence list both come from
the same “whatever the fetcher returned” pool, so a partial response
could be promoted into durable evidence even though the upstream
contract is “full cursor pagination + matching identity”.

Two distinct bugs:

1. The historical CLI
   `apps/api/scripts/backfill_no_trade_evidence.py` consumes
   `_fetch_year_moex` and treats its list as authoritative; today it
   records zero-trade evidence for whatever rows happened to come back.
   On a degraded network this propagates `partial` into `moex_no_trade_evidence`.
2. `BackfillRunner._process_moex_year` (the in-process historical
   walk) feeds the same partial lists into the evidence helper, so the
   bar commit and the evidence commit can both silently consume
   partial data. We must NOT regress bar-list compatibility — existing
   consumers that already tolerate partial bars must keep working.

Sample probes (production already taken, read-only) confirm that the
upstream indeed returns the explicit zero-trade row shape on every
genuine no-trade session for share / ETF / bond instruments, but the
probes also confirm the fetcher does not currently certify that the
response was complete. Without an explicit provenance outcome,
partial / error / malformed responses are indistinguishable from full
ones — and the durability of `moex_no_trade_evidence` is the source
of truth for the ML gate. We must fix the contract, not the data.

Standing goal context (from the verified investigation): we already
have one public capability for this surface. We are extending it, not
adding a new capability. Capability name remains `data-quality`. The
change adds two narrow requirements inside `data-quality` and threads
a new fetcher outcome through the historical walker; no public metric
is added, the cached `expected_bars` column stays authoritative, and
the writer-lock contract from the existing `writer-coordination`
capability stays the single source of truth for evidence serialization.

## What

1. New typed `MOEXFetchOutcome` (`complete | partial | error |
   malformed | identity_mismatch`) threaded through
   `apps.api.ingestion.backfill._fetch_year_moex` so every caller can
   tell whether the rows it holds came from a validated full
   pagination, an incomplete cursor, a network/HTTP error, a malformed
   payload (missing `TRADEDATE` column, no `history.cursor` and short
   page), or an identity-mismatch response (SECID / BOARDID differ
   from the ticker / board we asked for). The function still returns
   the rows it could parse — bar consumers that already tolerate
   partial bars continue to see them; evidence consumers can now branch
   on outcome and refuse to record zero-trade evidence from anything
   other than `complete`.
2. New helper `record_historical_no_trade_evidence(conn, *, db_path,
   figi, ticker, rows, board, isin, outcome, from_d, to_d, today=None) -> int`
   in `apps.api.ingestion.no_trade_evidence`. The explicit `ticker`
   argument is the contract that lets the helper perform the
   per-row identity filter without guessing from `rows[0]`. The
   helper accepts only `outcome == "complete"`; for every other
   outcome it returns `0` and performs no SQLite mutation. When
   accepted, it delegates to the existing
   `record_no_trade_evidence` so TTL semantics, recent /
   historical expiry branching, real-bar-wins filtering, and ON
   CONFLICT refresh behaviour are preserved verbatim.
3. Business-date filtering for the historical walk: the helper
   subtracts weekends and `moex_holidays` from the [from_d, to_d]
   window before deciding which zero-trade rows to record. A row that
   sits on a non-business day is rejected by query (no evidence).
   `fromisoformat`-malformed `ts` is rejected by query as well. This
   is the per-row mirror of the explicit “record evidence only on
   requested business dates” rule.
4. The historical walker
   (`BackfillRunner._process_moex_year` and `_process_one`
   historical walk branch) is refactored to:
   - capture the `MOEXFetchOutcome` from
     `_fetch_year_moex_outcome` per figi (the function is bound to
     the `BackfillRunner` instance as a method so the walker is
     statically bound to the new fetch symbol — the plan MUST
     define this binding in Task 2, not defer it to a later
     refactor);
   - record zero-trade evidence only when outcome is `complete` and
     every row's SECID matches the explicit `ticker` and BOARDID
     matches the figi board;
   - log a single structured line per rejected batch:
     `{event: moex_historical_evidence_rejected, figi, ticker, reason,
     rows}` where `reason ∈ {partial, error, malformed,
     identity_mismatch, non_business_date}`;
   - leave the `bars` list alone so the in-process backfill still
     writes whatever real bars the partial response had; bar count is
     unchanged for consumers that already tolerate partial bars.
   This is a **mandatory** integration, not an optional extension:
   the verified investigation's gate improvement is the new
   evidence path, and a helper-only change that leaves the in-
   process walker on the old list-only path does not move the
   gate. No claim that all 215 incomplete-history figis are
   recoverable is made or implied.
5. Writer lock: the new helper acquires the shared writer lock once
   through the existing `writer-coordination` infrastructure
   (`_evidence_writer_lock` / `writer_lock` context manager with
   `role="no-trade-evidence"` and `phase="evidence"`). No new lock path,
   no new role / phase, no nested acquisition. Listed-till UPDATE (for
   delisted instruments) and the evidence write remain two separate
   write-sections per the existing `writer-coordination` spec. The
   helper MUST accept an explicit `db_path`; there is no inferred
   production DB path shim.
6. Spec delta in `openspec/specs/data-quality/spec.md`:
   - ADDED Requirement: **Historical MOEX Evidence Validity Contract**
   - ADDED Requirement: **Historical Evidence Walk for Stale Figis**

## Impact

- `apps/api/src/algotrader_api/ingestion/backfill.py`
  (`_fetch_year_moex` returns a typed outcome alongside the rows)
- `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
  (new `record_historical_no_trade_evidence` helper that gates on
  outcome + business-date filter; existing `record_no_trade_evidence`
  unchanged)
- `apps/api/scripts/backfill_no_trade_evidence.py`
  (use outcome, branch on `complete`, log rejected batch)
- `apps/api/src/algotrader_api/ingestion/backfill.py`
  `BackfillRunner._process_moex_year` and `_process_one` historical
  walk branch (use outcome; same logging policy; bar list unchanged)
- `apps/api/tests/test_moex_no_trade_evidence.py`
  (new RED/GREEN tests for outcome, business-date filter,
  identity-mismatch rejection, helper independence from bar list)
- `apps/api/tests/test_backfill_no_trade_evidence_lock.py`
  (CLI outcome gating — partial / error / malformed leave evidence
  rows untouched)
- `apps/api/tests/test_moex_all_paths_identity.py`
  (extend with regression test: `_fetch_year_moex` carries the
  outcome; `identity_mismatch` outcome fires when SECID/BOARDID
  disagree)
- `openspec/specs/data-quality/spec.md`
  (two ADDED Requirements; no MODIFIED / REMOVED; no changes to
  cached `expected_bars`, 95 % gate, or staleness override)

## Out-of-goals

- No new MOEX HTTP requests; we reuse each full-year payload the
  walker already issues. The evidence path reads from the same
  row list the bar consumer reads; there is no second fetch per
  figi.
- No fabricated OHLCV bars; the `bars` table only ever gets real
  upstream data.
- No claim that all 215 incomplete-history figis are recoverable
  or that the daily 45-minute phase hang is caused by missing
  evidence. The change fixes the contract; the production walker
  still walks the same window. Completion is measured separately
  and on its own schedule.
- No CLI argument expansion: the existing
  `apps/api/scripts/backfill_no_trade_evidence.py` is updated to
  route through the new helper (so it picks up the outcome gate,
  the business-date filter, and the structured
  `moex_historical_evidence_rejected` line) but it does not gain
  new arguments, new exit codes, or new branches.
- No new public metric, no new cached `expected_bars` writes from
  the change itself (the end-to-end test runs
  `populate_expected_bars` on a TEMP DB to recompute the canonical
  `expected_bars`; production threshold / universe / expected
  formula are unchanged), no new scheduled phase, no new thread /
  timeout / budget.
- No changes to TTL semantics (recent 7 d / historical 365 d), the
  cached `expected_bars` denominator, the 95 % coverage threshold,
  or the writer-coordination lock namespace.
- No re-introduction of raw `INSERT INTO bars` on the backfill
  path; the writer-coordination raw-path removal stays.