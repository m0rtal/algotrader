# enforce-tradeable-classes-at-ingest

## Why

Pre-change, the universe drift loop hit the operator three times in
two weeks:

1. The Tinkoff SDK returned ~13k non-tradeable figis (futures +
   options) on every backfill run. `discover_universe` happily
   wrote them all into `instruments`.
2. The operator had to manually invoke `scripts/cleanup_universe.py`
   every time the universe drifted past 12k figis. Forget it once
   and `/api/admin/backfill/pending` shows 14k instruments, the
   Backfill tab overflows, and the rate-limit budget burns on rows
   we never wanted in the first place.
3. Cleanup-as-afterthought is structurally fragile: any layer that
   reads `instruments` between discovery and cleanup sees the
   drift.

The fix moves the filter from "post-hoc sweep" to "never written in
the first place". The contract lives in
`algotrader_api.domain.tradeable.TRADEABLE_CLASSES` — every layer
that touches the universe reads from there.

## What Changes

- `discover_universe` skips the broker SDK methods for
  non-tradeable classes (future/option) and filters any stray
  class on the way out.
- `upsert_instruments` filters again at the SQL boundary before
  the `INSERT OR REPLACE`.
- `_list_instruments` (in the backfill runner) restricts the
  backfill queue to `TRADEABLE_CLASSES` via `WHERE class IN (...)`.
- `algotrader_api.domain.tradeable` becomes the canonical home of
  `TRADEABLE_CLASSES`. `maintenance/cleanup` re-exports it for
  backwards compat but the runtime no longer relies on it for
  correctness — only for emergency sweeps.

## Impact

- `/api/admin/backfill/pending` no longer counts non-tradeable
  figis. The header strip's `INSTRUMENTS` count reflects what the
  backfill will actually fetch.
- `cleanup_universe.py` stays around as a defensive tool for the
  one-off case where the broker changes its class taxonomy and a
  stale row sneaks in. The runtime no longer depends on it.
- No schema migration; no new tables; no new dependencies.

## Non-Goals

- We are not adding currency / commodity classes. The operator
  trades shares, ETFs, and bonds; the spec reflects that.
- We are not changing the backfill retry semantics or the rate
  limiter. The filter is a pure gate; everything downstream is
  untouched.
- We are not removing `cleanup_universe.py` — it remains the
  documented operator path for a deliberate universe-wide sweep.
