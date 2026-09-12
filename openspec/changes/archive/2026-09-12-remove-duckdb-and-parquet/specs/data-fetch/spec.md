# data-fetch Specification (delta)

## REMOVED Requirements

### Requirement: Tickers overview is computed from on-disk parquet via DuckDB

The system read the `/api/tickers` overview by globbing
`data/bars/*.parquet` through DuckDB and grouping by ticker. This
implementation is replaced by a SQLite-native aggregate query.

#### Scenario: Tickers overview latency

- GIVEN 3717 parquet files totaling 42.98 MB on disk
- WHEN the UI requests `/api/tickers`
- THEN the server responds in less than 100 ms (current baseline
  is 1.7 s+ — see Why in proposal.md)
