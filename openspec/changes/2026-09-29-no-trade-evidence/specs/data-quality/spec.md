## ADDED Requirements

### Requirement: Per-figi no-trade evidence from MOEX ISS

The system SHALL persist per-figi, per-session-date evidence that
MOEX ISS confirmed the issuer had no trades on its primary tradable
board on that date, separately from the `bars` table. The evidence
SHALL be used by the consumption-time coverage gate to:

- accept a figi whose `MAX(bars.ts)` precedes the last completed
  business day provided a continuous chain of confirmed evidence
  rows covers every session between `MAX(bars.ts)` and the last
  completed session; and
- subtract confirmed no-trade dates from the historical expected
  denominator when computing per-figi coverage.

#### Scenario: Zero-trade row from MOEX produces evidence

- GIVEN MOEX ISS returns a successful response with full pagination
  for a figi's primary board
- AND the row for `session_date` has `SECID == ticker`,
  `BOARDID == board`, all OHLC values `NULL`, and `VOLUME`,
  `NUMTRADES`, `VALUE` all equal to 0
- AND `instruments.isin` for the figi equals the MOEX description
  ISIN for that ticker
- WHEN `BackfillRunner.backfill_moex_recent_tail` processes the figi
- THEN a row `(figi, session_date, board, isin, observed_at, expires_at)`
  is inserted into `moex_no_trade_evidence`
- AND no row is inserted into `bars`

#### Scenario: A real bar suppresses a stale evidence row

- GIVEN `moex_no_trade_evidence` contains a row for `(figi, session_date)`
- WHEN `replace_bars_for_figi` writes a `bars` row for the same
  `(figi, session_date)`
- THEN `moex_no_trade_evidence` no longer contains that pair
- AND the suppression is idempotent (a second bar write does not
  raise)

#### Scenario: Empty / error / partial MOEX response produces no evidence

- GIVEN MOEX ISS returns an empty page, HTTP error, truncated
  pagination, or a row whose `SECID`/`BOARDID` does not match the
  figi's primary board
- WHEN `BackfillRunner.backfill_moex_recent_tail` processes the figi
- THEN no row is inserted into `moex_no_trade_evidence`
- AND the figi is treated as "unknown" by the coverage gate (the
  gate fails closed)

#### Scenario: Staleness check accepts a continuous evidence chain

- GIVEN `bars.MAX(ts) = 2026-09-25` for a figi
- AND the last completed MOEX business day is `2026-09-28`
- AND `moex_no_trade_evidence` contains rows for `2026-09-26` and
  `2026-09-28` (and no row for `2026-09-27`)
- WHEN `check_coverage([figi])` runs
- THEN the figi is reported as `stale`

#### Scenario: Staleness check accepts a complete chain

- GIVEN `bars.MAX(ts) = 2026-09-25` for a figi
- AND the last completed MOEX business day is `2026-09-28`
- AND `moex_no_trade_evidence` contains rows for `2026-09-26`,
  `2026-09-27`, and `2026-09-28`
- WHEN `check_coverage([figi])` runs
- THEN the figi is NOT reported as `stale`
- AND it remains subject to the `incomplete` check against the
  cached `expected_bars` column

#### Scenario: Evidence has a finite re-validation deadline

- GIVEN a row exists in `moex_no_trade_evidence` with
  `expires_at < date('now')`
- WHEN `check_coverage([figi])` runs
- THEN that row is ignored
- AND the figi is treated as "unknown" for the staleness check
- AND no expiration roll-off is applied to `bars`

#### Scenario: Cached expected_bars is unchanged by evidence

- GIVEN `instruments.expected_bars = N` for a figi
- AND `moex_no_trade_evidence` contains K rows for the figi
- WHEN `check_coverage([figi])` runs
- THEN the 95 % completeness ratio uses `N` as the denominator
- AND the cached column is not modified by the gate
