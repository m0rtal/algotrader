# dev-workflow Specification (delta)

## ADDED Requirements

### Requirement: Every corporate_actions row carries a verifiable source

The system SHALL persist a `source` column on the `corporate_actions`
table. Every row inserted via an importer MUST set `source` to a
machine-verifiable string that includes the provider identifier
(`tinkoff`, `moex:iss`, or `manual`) and the data type (`dividends`,
`split-diff`). For `split-diff` rows, the source string SHALL include
the two snapshot timestamps used to compute the factor so the factor
is reproducible.

Rows WITHOUT a `source` value SHALL be removed by a one-time
backfill migration. No importer SHALL write a row with
`source = NULL`.

#### Scenario: MOEX ISS dividends importer tags every row

- GIVEN `python -m scripts.import_corporate_actions_moex state.db`
- WHEN a dividend event is fetched from MOEX ISS
- THEN the resulting `corporate_actions` row has
  `source = 'moex:iss:dividends'`

#### Scenario: Tinkoff SDK dividends importer tags every row

- GIVEN `ALGOTRADER_TINKOFF_ENABLE=1` is set
- WHEN `python -m scripts.import_corporate_actions_tinkoff state.db`
  is run
- THEN every resulting `corporate_actions` row has
  `source = 'tinkoff:dividends'`

#### Scenario: split-detection diff tags with both snapshot timestamps

- GIVEN two snapshots for figi X with face_value 1.0 (ts=T1) and 50.0
  (ts=T2)
- WHEN the diff detector runs
- THEN the resulting `corporate_actions` row has
  `source = 'moex:iss:split-diff:T1:T2'` and `factor = 50.0`

#### Scenario: rows without source are removed

- GIVEN the `corporate_actions` table contains rows with `source IS NULL`
- WHEN the one-off backfill script
  (`scripts.migrate_corporate_actions_source`) runs
- THEN those rows are deleted
- AND every remaining row has a non-null `source`

#### Scenario: no importer writes NULL source

- GIVEN any importer (`moex`, `tinkoff`, or split-diff)
- WHEN it constructs a `CorporateActionRow`
- THEN the `source` field is set to a non-null string
- AND a unit test asserts the row's `source` field is populated
