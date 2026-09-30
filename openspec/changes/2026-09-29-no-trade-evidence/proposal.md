# Proposal: Per-figi no-trade evidence from MOEX ISS

## Why

The ML coverage gate (Spec: data-quality, Pre-Consumption Coverage Gate)
treats an absent bar on the last completed MOEX business day as a stale
figi, and a low `bars_count` against a positive `expected_bars` cache as
incomplete. For a bond/ETF/share whose issuer literally did not trade on
that day (no orders filled, only opening auction orders cancelled,
issuer holiday, etc.) MOEX ISS returns a single explicit zero-trade row
with all OHLC values `None` and `VOLUME=NUMTRADES=VALUE=0`. The current
pipeline treats that response the same way it treats "MOEX has no data
at all" — the figi is left stale forever, the daily chain retries
indefinitely, and the operator screen shows `N stale` even though the
figi's market was simply closed.

The fix: when MOEX ISS confirms a zero-trade session with the figi's
exact `SECID` + `BOARDID` + ISIN, persist that fact as a separate
**evidence** row. The ML gate subtracts confirmed no-trade sessions from
the expected denominator (and treats a continuous confirmed chain as
fresh for the staleness check). Real bars always win — when a real bar
ever lands for `(figi, session_date)`, the evidence row is removed.

## What

1. New SQLite table `moex_no_trade_evidence` populated by the
   recent-tail pass and the historical walk when MOEX returns an
   explicit zero-trade row.
2. New module
   `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py` with
   three responsibilities:
   - confirm the upstream shape (full pagination, matching identity,
     exactly-zero counters);
   - upsert evidence rows with an `expires_at` deadline (recent
     evidence 7 days, historical evidence 365 days);
   - reconcile against the `bars` table so a real bar always wins.
3. `_fetch_year_moex` carries the raw upstream columns
   (`SECID`, `BOARDID`, `NUMTRADES`, `VALUE`) on each emitted dict so
   the helper can confirm identity without a second HTTP round-trip.
4. `BackfillRunner.backfill_moex_recent_tail` records evidence as
   part of its normal write path.
5. `replace_bars_for_figi` reconciles evidence after every commit so a
   later real bar always suppresses a stale evidence row.
6. `ml.features.check_coverage` accepts confirmed no-trade evidence
   for the staleness check (continuous chain from `max_ts` to
   `last_session`) but the cached `expected_bars` column stays
   authoritative for the 95 % completeness ratio. The
   `populate_expected_bars` script remains the single source of truth
   for the cached denominator; evidence only adjusts the staleness
   arm of the gate.

## Impact

- `apps/api/src/algotrader_api/db/migrations/025_moex_no_trade_evidence.sql` (new)
- `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py` (new)
- `apps/api/src/algotrader_api/ingestion/backfill.py` (raw columns threaded through `_fetch_year_moex`; recent-tail records evidence)
- `apps/api/src/algotrader_api/db/bars_sqlite.py` (reconcile evidence after bar writes)
- `apps/api/src/algotrader_api/ml/features.py` (staleness override with evidence chain)
- `apps/api/tests/test_moex_no_trade_evidence.py` (new)
- Spec change: `openspec/specs/data-quality/spec.md` adds the
  "Per-figi no-trade evidence" requirement.

## Non-goals

- No new MOEX HTTP requests (the recent-tail fetch already covers the
  trailing 10–14 calendar days; the historical walk covers the rest).
- No fabricated OHLCV bars. The bars table only ever gets real
  upstream data.
- No new class-level filtering. All four tradeable classes continue
  to flow through the same queue.
- No changes to `expected_bars` caching semantics — the cached column
  remains the canonical denominator for the 95 % threshold.
