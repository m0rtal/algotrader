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
| `apps/api/src/algotrader_api/ingestion/backfill.py` | Returns `MOEXFetchOutcome` alongside `list[dict]`; outcome is a `Literal` alias; the loop assigns it per page and reduces it via a small `_reduce_outcomes` helper that records the worst severity (error > malformed > partial > identity_mismatch > complete). |
| `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py` | New `record_historical_no_trade_evidence` helper; new `MOEXFetchOutcome` `Literal` re-exported from the same module so callers import one symbol. Business-date filter helper `_is_business_date_for_evidence` (weekend + `moex_holidays`) lives next to it. |
| `apps/api/scripts/backfill_no_trade_evidence.py` | CLI: pass outcome through, gate on `complete`, log structured `moex_historical_evidence_rejected` line per rejected batch, leave bars alone. |
| `apps/api/tests/test_moex_no_trade_evidence.py` | New RED/GREEN tests for outcome gating, business-date filter, identity-mismatch rejection, and bar-list independence. |
| `apps/api/tests/test_backfill_no_trade_evidence_lock.py` | New tests: CLI returns `0 rows` and exits `0` on partial / error / malformed / identity_mismatch outcomes; writes no evidence rows; emits the structured log key. |
| `apps/api/tests/test_moex_all_paths_identity.py` | Regression: `_fetch_year_moex` carries the outcome; SECID / BOARDID disagreement returns `identity_mismatch`. |

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
   completeness on its own).
3. `partial` — cursor present, but `offset + len(rows) < total`
   (pagination incomplete).
4. `identity_mismatch` — at least one row carries `SECID != ticker` or
   `BOARDID != board` (this is a per-row check; it is reported at the
   call level when at least one row mismatches).
5. `complete` — all pages parsed cleanly, all SECID / BOARDID
   matched the request, and the final cursor showed
   `offset + len(rows) >= total`.

Reduction is total and stable so existing partial-counting
`_process_moex_year` keeps its bar totals identical for
non-`complete` outcomes (consumer compatibility) and only the
evidence path branches.

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
  “failure to render”.
- A partial response can include rows for the requested dates and
  rows for past dates that MOEX spilled in. The existing
  `_fetch_moex_range` post-filter keeps only `[from_d, to_d]` rows;
  the evidence helper now applies the same filter via the
  business-date check.
- `MOEXFetchOutcome` is intentionally a `Literal` alias (not an Enum)
  to match the convention used by the existing `WriterRole` /
  `WriterPhase` types in `writer_lock.py` — no runtime indirection
  layer in this hot loop.
- The helper MUST be honest about identity: when `_fetch_year_moex`
  reports `identity_mismatch`, the helper MUST reject the entire
  batch even if some rows have correct identity. Identity is a
  per-batch property: a single cross-listed mirror row poisons the
  whole fetch.
- Tests must remain `:memory:`-friendly where they don’t assert
  interprocess locking. The new outcome / business-date /
  identity-mismatch tests run against the private SQL helper and
  must not require the writer lock. The CLI outcome-gating test
  DOES need a file-backed temp DB so the kernel-level `flock` is
  exercised for the existing evidence wrapper.