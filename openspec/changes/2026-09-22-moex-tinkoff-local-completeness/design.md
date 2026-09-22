# Design

## Source-selection rule (`source="auto"`, default)
For each figi:
1. Probe `BackfillRunner._get_meta_moex(ticker)` (already implemented at
   `apps/api/src/algotrader_api/ingestion/backfill.py:169`). Returned
   dict: `{market, board, listed_from, listed_till}`.
2. If probe returns `None` (sanctions-delisted, MOEX has no boards for
   the ticker): fall back to Tinkoff for the full window via
   `BackfillRunner._fetch_tinkoff_fallback` (already implemented).
3. Else: window = `[listed_from, yesterday]`. Split into
   `[listed_from, max(earliest_local_bar - 1, listed_from + 8 months)]`
   (MOEX) ∪ `[earliest_local_bar, yesterday]` (Tinkoff). If no local
   bars exist yet, MOEX covers the full window.

## Source-selection rule (`source="moex"`)
Same as auto but skips the Tinkoff tail (caller wants pure-MOEX audit).

## Source-selection rule (`source="tinkoff"`)
Existing behaviour, retained for the operator escape hatch.

## Idempotency
Existing `replace_bars_for_figi(..., replace=False)` uses
`INSERT OR IGNORE` on `(figi, ts)`. Re-running the same backfill adds
zero rows. The new pass is purely additive.

## Concurrency
Existing `parallel_limit = 10` semaphore is preserved. MOEX year-by-year
pagination happens inside one figi's worker — no cross-figi fan-out
change.

## Out of scope
- Multi-worker runner coordination (single-process MVP, documented in
  `apps/api/src/algotrader_api/routes/backfill.py` module docstring).
- Intraday (1-min / hour) bars. Daily only.
- Bars for non-tradeable figi classes (`futures`, `options`). The 16
  current figis are all `share`.

## Risk: 5-year Tinkoff attempt leaves a `skipped` marker
`BackfillRunner._backfill_one` (line 1144-1158) marks the figi
`status="skipped"` and stamps `last_bar_ts=today` on empty response.
That blocks the new pass via `decide_strategy`. The fix lives in
Task 2: `_backfill_one` must NOT mark `skipped` when the empty
response came from a window that should have gone through MOEX. The
distinguishing condition: `client` is the Tinkoff client AND the
window is longer than 7 days. New `source` param on `_backfill_one`
makes the call site explicit.
