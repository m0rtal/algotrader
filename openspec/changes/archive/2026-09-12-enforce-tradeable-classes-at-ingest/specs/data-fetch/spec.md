# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: Tradeable asset classes are filtered at the ingest boundary

The system SHALL restrict every layer that touches the
`instruments` table to the `TRADEABLE_CLASSES` set
(`{"share", "etf", "bond"}`). Adding a new class (e.g. `currency`)
requires a spec change in `data-fetch` and a corresponding test
in `tests/test_tradeable.py`.

#### Scenario: discover_universe skips non-tradeable broker methods

- GIVEN the broker SDK exposes `get_shares`, `get_bonds`,
  `get_etfs`, `get_futures`, and `get_options`
- WHEN `discover_universe` runs
- THEN only `get_shares`, `get_bonds`, and `get_etfs` are called
- AND `get_futures` and `get_options` are not called
- AND the returned list contains only rows whose `class` is in
  `TRADEABLE_CLASSES`
- AND a `universe.class.skipped` log line is emitted for each
  non-tradeable class

#### Scenario: upsert_instruments filters rows at the SQL boundary

- GIVEN a caller hands `upsert_instruments` a list containing rows
  for `share`, `bond`, `etf`, `future`, and `option`
- WHEN the function runs
- THEN only share/bond/etf rows land in `instruments`
- AND a `universe.class.post_filter_dropped` warning is emitted

#### Scenario: _list_instruments restricts the backfill queue

- GIVEN `instruments` contains rows for share/bond/etf/future/option
  (e.g. left over from before the policy change)
- WHEN the backfill runner builds its queue
- THEN only rows whose `class` is in `TRADEABLE_CLASSES` are
  returned
- AND the SQL query is `SELECT ... WHERE class IN (?, ?, ?)`
  rather than `SELECT ...`

#### Scenario: TRADEABLE_CLASSES is the single source of truth

- GIVEN `algotrader_api.domain.tradeable.TRADABLE_CLASSES` is the
  canonical definition
- WHEN any other module needs to know what's tradeable
- THEN it imports from `domain.tradeable`, not from a local copy
- AND `maintenance/cleanup.py` re-exports the constant for
  backwards compatibility with existing tests and scripts
