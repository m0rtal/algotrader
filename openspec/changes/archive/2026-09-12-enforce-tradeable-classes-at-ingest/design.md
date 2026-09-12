# Design: enforce-tradeable-classes-at-ingest

## Stack

- New module `algotrader_api.domain.tradeable` — single source of
  truth for `TRADEABLE_CLASSES`, plus `is_tradeable` and
  `filter_tradeable` helpers.
- No new dependencies. No schema migration. No new routes.
- Tests: `tests/test_tradeable.py` (unit), updates to
  `tests/test_backfill.py`, `tests/test_universe_filter.py`,
  `tests/ingestion/test_universe.py`,
  `tests/ingestion/test_universe_coverage.py`.

## Layout

```
apps/api/src/algotrader_api/
├── domain/
│   ├── __init__.py            (NEW)
│   └── tradeable.py           (NEW: TRADEABLE_CLASSES, is_tradeable, filter_tradeable)
├── ingestion/
│   ├── universe.py            (UPDATE: discover_universe skips non-tradeable; upsert_instruments filters)
│   └── backfill.py            (UPDATE: _discover_universe skips non-tradeable; _list_instruments filters on SQL)
└── maintenance/
    └── cleanup.py             (UPDATE: import + re-export TRADEABLE_CLASSES)
```

## Data flow

The filter is applied at four points — three runtime, one test:

1. **`discover_universe`** (ingestion/universe.py): iterate over
   the broker's five `get_<class>` methods, **skip the call** if
   `class not in TRADEABLE_CLASSES`, and **filter the result
   again** before returning. Belt + braces: even if the SDK
   starts returning a new class the policy doesn't know about,
   the row never reaches `instruments`.

2. **`upsert_instruments`** (ingestion/universe.py): `filter_tradeable(rows)`
   before the `INSERT OR REPLACE` loop. Last line of defence for
   any caller that bypasses `discover_universe` (mostly tests).

3. **`_list_instruments`** (ingestion/backfill.py): `WHERE class IN
(...)` on the SQL boundary. Stale rows from before this change
   cannot slip through.

4. **`_discover_universe`** (ingestion/backfill.py): the
   in-runner path duplicates `discover_universe`'s loop. Same
   `if class not in TRADEABLE_CLASSES: continue` guard.

## Trade-offs

- We add one more import (`from ..domain.tradeable import
TRADEABLE_CLASSES`) in three files. The benefit is one source
  of truth; the cost is one line of plumbing per call site.
- We keep `cleanup_universe.py` as a manual operator tool. It's
  no longer required for correctness, but operators may still
  want to force a universe-wide sweep after a broker class
  taxonomy change.

## Testing

- `test_tradeable.py`: 7 unit tests covering the contract
  (`TRADEABLE_CLASSES` set, `is_tradeable`, `filter_tradeable`,
  null handling).
- `test_universe_filter.py` (new): direct tests that
  `upsert_instruments` filters non-tradeable rows and that an
  all-non-tradeable list returns 0 inserts.
- `test_universe.py` / `test_universe_coverage.py`: rewritten
  assertions — `discover_universe` returns only tradeable
  classes even when the InMemoryTinkoffClient has futures/options
  set.
- `test_backfill.py::test_discover_universe_returns_total_instrument_count`:
  asserts that `get_futures` / `get_options` are never called on
  the client.
- `test_backfill.py::test_list_instruments_returns_tradeable_only`
  (renamed): asserts that `_list_instruments` excludes
  non-tradeable rows at the SQL boundary.

## Rollback

Revert the four guards. The runtime immediately reverts to
"fetch all five classes, store everything, rely on cleanup".
Operators who already have a clean DB stay clean; operators who
have a dirty DB need to invoke `scripts/cleanup_universe.py`.
