# data-quality-integrity — Proposal

## Why

Backtests on stored OHLCV bars are only as trustworthy as the bars
themselves. Today the algotrader pipeline writes whatever the broker
returns into `bars` and never re-checks the result. Two failure modes
are operationally real:

1. **Corporate-action blindness.** A `2-for-1` split produces a raw
   ~50% price drop in the candle series. Backtests that don't
   pre-adjust close will treat it as a real signal and lose money.
   There is no `corporate_actions` table and no `adj_close` column
   or view. Adjustments are an operator's responsibility; we have no
   plumbing for them at all.
2. **Broker-side corruption slipping through.** Tinkoff occasionally
   returns candles that violate obvious consistency rules
   (`high < open`, `volume < 0`, all-zero rows on active tickers).
   The current pipeline writes them to `bars` and moves on. The data
   quality guardian issues `MISSING_RECENT` / `SPARSE_HISTORY`
   problems but says nothing about rows that are mathematically
   impossible. A trader running backtests over such data gets wrong
   signals and no warning.

We need a single change that (a) makes adjustments a first-class
SQLite artifact (table + view + load script), and (b) validates
every fetched bar at ingest and surfaces violations through the
existing health endpoint.

## What Changes

1. **New `corporate_actions` table** keyed on `(figi, action_type,
   ex_date)`. Idempotent loader from
   `apps/api/scripts/data/corporate_actions.json` (operator-maintained,
   same pattern as `moex_holidays.json`).
2. **New `bars_adjusted` view** that LEFT JOINs `corporate_actions`
   and computes the Chicago Booth-style backward adjustment factor
   per bar (split events only; dividends are recorded but not folded
   into the adjusted price in this change).
3. **New `data_quality/integrity.py`** with `validate_bar(figi, bar)
   -> list[IntegrityViolation]` enforcing five deterministic rules:
   `high-below-o-h-l-c`, `low-above-o-h-l-c`, `volume-negative`,
   `all-zero`, `missing-field`.
4. **New `HealthIssue.BAR_CORRUPTION`** with `-40` penalty,
   surfaced by `compute_health` whenever any stored bar for the
   figi violates the integrity rules.
5. **`BackfillRunner._backfill_one`** runs `validate_bar` on every
   fetched candle before `INSERT`. Corrupt candles are skipped and
   recorded in `ingestion_logs` with `level='warn'`.
6. **Operator runbook** paragraph in `CONTRIBUTING.md` documenting
   `python -m scripts.import_corporate_actions` and the corruption
   self-check.

Capability boundary: this introduces a new `data-quality` spec for
the integrity + adjustment contract. Implementation modules live in
`apps/api/src/algotrader_api/data_quality/` (the package already
exists from the `data-quality-guardian` change; we extend it).
Migrations run after `006_moex_holidays.sql` and are numbered `007`
and `008`.

## Capabilities

### New Capabilities

- `data-quality`: Defines how the system persists corporate actions,
  exposes an adjusted-close view, validates bars at ingest, and
  reports stored-bar corruption as a `HealthIssue`.

### Modified Capabilities

_None._ The `data-fetch` capability is not changed by this proposal;
the integrity rules and adjusted-close view are surfaced as new
requirements under the new `data-quality` capability rather than as
amendments to `data-fetch`.

## Impact

- **Backtest fidelity**: anyone running `/api/bars/<symbol>` for a
  figi with a recent split can now opt into
  `bars_adjusted.adj_close` instead of patching factors by hand.
- **Operator visibility**: the daily guardian emits
  `BAR_CORRUPTION` whenever stored bars violate an integrity rule,
  so silent broker-side corruption is no longer invisible.
- **Ingest path**: `BackfillRunner._backfill_one` now runs `validate_bar`
  per candle; the additional cost is O(1) per bar (5 rule checks)
  with no extra DB roundtrip.
- **DB schema**: two new SQL objects (`corporate_actions` table,
  `bars_adjusted` view). `bars` itself is unchanged.
- **Tests**: ~5 new fixtures + sanitisation of any existing fixture
  bars that violate the new rules.

## Non-Goals

- **Adjusted OHLC (open/high/low).** Only `close` is
  backward-adjusted in this change. Full adjusted OHLC requires
  multiplying O/H/L by the same factor as close.
- **Total-return / dividend-reinvested close.** Adds cumulative
  cash dividends to the price series. Different algorithm, different
  table.
- **Cross-source reconciliation** (Tinkoff vs MOEX ISS vs Finam).
- **Auto-import of corporate actions** from
  `client.get_dividends`. The JSON is hand-maintained for now.
- **Realtime/live bar integrity validation.** The check runs at
  ingest (BackfillRunner), not on `/api/bars/<symbol>` GETs.

## Risks

- **Existing data may already contain corrupt bars.** First run
  after the migration will surface every stored violation. Operationally
  expected; documented in CONTRIBUTING.md.
- **Backfill fixture cleanup.** Several existing tests may use bars
  that violate the new rules (`high < open`). Each must be sanitised
  or the test will break. Expected to take ~1-2 hours of fixture work.
- **`bars_adjusted` performance.** Computes a correlated subquery
  per row. Tolerable at current scale (~2.4 M rows × ~1 split per
  figi); not benchmarked. If `/api/bars/<symbol>` slows down, cache
  `adj_close` in a real column instead of a view.
