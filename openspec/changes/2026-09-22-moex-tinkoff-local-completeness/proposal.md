# Proposal: Local MOEX + Tinkoff data completeness

## Why
The `bars` table holds 180 daily rows per figi from a single Tinkoff run
covering 2025-09-16..2026-05-25. We need the full contiguous daily series
from each figi's MOEX `listed_from` to yesterday so the backtester, the
data-quality guardian, and the UI's coverage gap visualisation all read
from the same dataset. The 5-year MOEX backfill attempt on 2026-09-16
returned zero candles for all 16 figis — the runner walked the window
via the Tinkoff client, which caps day-interval requests at ~7 days and
returned empty.

## What
A source-switching layer in `BackfillRunner.run()` and
`data_quality.completeness.run_completeness_pass`. The runner walks
history year-by-year through `BackfillRunner._fetch_year_moex` (already
implemented, MOEX ISS cursor pagination), then bridges the last ~9 months
with `client.get_candles` (Tinkoff) so today's bar lands.

## Impact
- `apps/api/src/algotrader_api/ingestion/backfill.py` (routing logic)
- `apps/api/src/algotrader_api/data_quality/completeness.py` (routing)
- `apps/api/src/algotrader_api/ingestion/priority.py` (no source change —
  already gap-aware)
- 1 new HTTP field on `POST /api/admin/backfill/start`: `source` ∈
  `{"auto","moex","tinkoff"}` (default `"auto"`). Existing callers
  unchanged because default is `"auto"`.
