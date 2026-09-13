# data-fetch Specification (delta)

## ADDED Requirements

### Requirement: Corporate actions are sourced from multiple providers and merged via a common writer

The system SHALL populate the `corporate_actions` table from at least
three sources, all writing through a single common helper
`merge_into_corporate_actions(db_path, rows)` which uses
`INSERT OR REPLACE` on the `(figi, action_type, ex_date)` primary key:

1. The bundled `apps/api/scripts/data/splits_curated.json` — a static
   list of historical splits for Russian and global paper not
   available from any free API.
2. The Tinkoff Invest SDK
   (`client.instruments.get_dividends(figi, from_, to)`) — covers
   every figi known to the broker; sandbox-friendly.
3. The MOEX ISS REST API
   (`GET /iss/securities/{secid}/dividends.json`) — free, no auth;
   `figi → secid` is resolved via `instruments.ticker`.

#### Scenario: each source writes through the common helper

- GIVEN the `corporate_actions` table is empty
- WHEN `python -m scripts.import_splits_curated state.db` is run
- THEN `CorporateActionRow(figi=..., action_type='split', ...)` is
  constructed from each JSON entry
- AND `merge_into_corporate_actions` is called with all rows
- AND the table has 17 rows after the call

#### Scenario: re-running an importer is idempotent

- GIVEN `corporate_actions` has 17 rows from the curated importer
- WHEN `python -m scripts.import_splits_curated state.db` is run a
  second time
- THEN no error is raised
- AND the table still has 17 rows
- AND no duplicates exist (PK constraint + INSERT OR REPLACE)

#### Scenario: source conflict is resolved by last writer

- GIVEN a `(figi, action_type, ex_date)` row exists with
  `cash_amount=100.0, note='old'`
- WHEN another importer writes the same PK with
  `cash_amount=387.0, note='new'`
- THEN the row is updated (INSERT OR REPLACE) to `cash_amount=387.0,
  note='new'`
- AND no duplicate row is created

#### Scenario: MOEX importer skips figis with no resolvable secid

- GIVEN a figi with `instruments.ticker = NULL` (e.g. recent IPO not
  yet indexed)
- WHEN `python -m scripts.import_corporate_actions_moex state.db` is run
- THEN the importer silently skips that figi
- AND no error is raised

#### Scenario: MOEX importer survives network failures

- GIVEN the MOEX ISS endpoint is unreachable
- WHEN `python -m scripts.import_corporate_actions_moex state.db` is run
- THEN the importer logs no errors and exits 0
- AND the rows from previously-imported sources remain in the table

### Requirement: Corporate action sources are exposed as operator scripts with idempotent semantics

The system SHALL expose each of the three sources as a
`python -m scripts.<name>` entry point backed by an in-package
implementation in `algotrader_api.scripts_import`. Every operator
script MUST be safe to re-run without duplicating rows.

#### Scenario: operator runs all three scripts in sequence

- GIVEN the `corporate_actions` table is empty
- WHEN the operator runs, in order:
  - `python -m scripts.import_splits_curated state.db`
  - `python -m scripts.import_corporate_actions_tinkoff state.db`
  - `python -m scripts.import_corporate_actions_moex state.db`
- THEN the table has at minimum the curated splits (17)
- AND any dividends returned by Tinkoff
- AND any dividends returned by MOEX ISS for the same figis

#### Scenario: re-running the same script is a no-op on existing rows

- GIVEN a row exists from a previous run
- WHEN the operator runs the same script again
- THEN the table row count does not increase (no duplicates)
- AND any updated values (e.g. `cash_amount` correction) are written
