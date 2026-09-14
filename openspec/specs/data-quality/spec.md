# Data quality

## Purpose

The data-quality capability ensures that the algorithmic-trading pipeline
operates against complete, consistent, and verifiable market data. It
covers:

- **Health scoring** — per-ticker health reports that surface sparse
  history, gaps, missing recent days, orphan data, and incomplete
  history.
- **Integrity validation** — bar-level rules that reject corrupt OHLCV
  rows before they reach backtests.
- **Recovery queue** — prioritised list of figis needing backfill,
  ordered by health score × expected bars per day.
- **Daily guardian** — systemd-style timer that runs the recovery queue
  and a completeness backfill pass once per day.
- **Corporate-action ingestion** — historical splits (derived from
  bars) and dividends (Tinkoff / MOEX ISS) so backtests can compute
  `adj_close`.

## Requirements

### Requirement: Historical splits are derived from local bars

The system SHALL detect historical stock splits and consolidations by
diffing consecutive `bars` rows in the local SQLite `bars` table, with
the **current** `face_value` fetched from MOEX ISS as a verification
cross-check.

#### Scenario: Single 2-for-1 split detected

Given `bars` for figi `X` contain rows `close=100` at `t-1` and
`close=50` at `t`, AND the current `face_value` for `X` is `2.0` (was
`1.0` before the split),
When the derivation runs,
Then `corporate_actions` SHALL contain one row
`(figi='X', action_type='split', ex_date=t, factor=2.0, source LIKE 'derived:bars+%')`.

#### Scenario: Reverse 10-for-1 split detected

Given `bars` for figi `Y` contain rows `close=5` at `t-1` and `close=50`
at `t`, AND `volume` is unchanged across the transition,
When the derivation runs,
Then `corporate_actions` SHALL contain one row
`(figi='Y', action_type='split', ex_date=t, factor=10.0, source LIKE 'derived:bars+%')`.

#### Scenario: Sub-threshold price changes ignored

Given `bars` for figi `Z` contain rows `close=100` at `t-1` and
`close=140` at `t` (ratio 1.4 — within the 2× threshold),
When the derivation runs,
Then no `corporate_actions` row SHALL be written for `Z` at `t`.

#### Scenario: Multiple splits on a single ticker

Given `bars` for figi `W` contain three transitions, each with
`ratio <= 0.5` or `ratio >= 2.0`,
When the derivation runs,
Then `corporate_actions` SHALL contain one row per detected transition.

#### Scenario: Bonus issue (BONU) is not misclassified as reverse split

Given `bars` for figi `V` contain `close=100, volume=1000` at `t-1` and
`close=10, volume=10000` at `t` (volume scaled by 10× same as price
ratio),
When the derivation runs,
Then no `corporate_actions` row SHALL be written for `V` at `t`
(treated as BONU, not reverse split).

#### Scenario: Re-run is idempotent

Given `corporate_actions` already contains a `derived:*` row for figi
`X` at `t`,
When the derivation runs again,
Then no new row SHALL be written and the existing row SHALL be
unchanged.
