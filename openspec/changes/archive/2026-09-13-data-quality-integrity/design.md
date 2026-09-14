# Design: data-quality-integrity

## Context

The `data_quality` Python package exists today (from the
`data-quality-guardian` change). It computes per-figi `HealthReport`
values and surfaces them via `GET /api/data-quality/{symbol}` and the
daily guardian. Its sub-problems are about *gaps* in coverage
(`MISSING_RECENT`, `SPARSE_HISTORY`, `HAS_GAPS`,
`RATE_LIMITED_FAILURES`) and *staleness* of metadata
(`ORPHAN_OK`). It has no notion of *internal consistency* of an
individual bar — that's what this change introduces.

Today:

- The `bars` table stores whatever the broker returns. The
  `BackfillRunner._backfill_one` writes candles via
  `replace_bars_for_figi` with no validation.
- The data-fetch spec has no `corporate_actions` table and no
  backward-adjusted close. Adjustments are a mental exercise for
  whoever runs a backtest.
- SQLite has no `ingestion_logs` row produced when bars are
  discarded. A corrupt bar goes in silently.

We want:

1. A `corporate_actions` table + backward-adjusted view that backtests
   can `SELECT` from.
2. A deterministic, unit-test-covered `validate_bar` that catches the
   obvious broker-side corruption patterns at ingest.
3. A `BAR_CORRUPTION` `HealthIssue` for the daily guardian so stored
   corruption isn't invisible.

## Goals / Non-Goals

**Goals:**

- `validate_bar` runs on every fetched candle before `INSERT`.
- Skipped candles are recorded in `ingestion_logs` with `level='warn'`.
- `compute_health` returns `BAR_CORRUPTION` for any figi whose stored
  bars contain at least one violated rule.
- `bars_adjusted.adj_close` matches a hand calculation for a 2-for-1
  split.
- The corporate-action loader is idempotent.
- Coverage stays ≥95%; all tests still pass.

**Non-Goals:**

- Adjusted OHLC (only `close`).
- Dividend-reinvested total return.
- Cross-source reconciliation.
- Live/streaming integrity validation (ingest-time only).
- Auto-loading corporate actions from the SDK.

## Decisions

### Decision 1: New canonical capability `data-quality`

The `data-quality-guardian` change shipped its delta under `data-fetch`
because the guardian is part of the data-acquisition flow. Corporate
actions + adjusted close + per-bar validation are a different concern:
they describe the *shape* of stored data, not how it was acquired.
Splitting them into a `data-quality` canonical keeps the `data-fetch`
spec readable (it's already large) and aligns the spec map with the
Python package map (`algotrader_api/data_quality/`).

A delta that targets a non-existent capability creates it; the apply
step writes `openspec/specs/data-quality/spec.md` with the new
`Purpose` and `Requirements` sections. No conflict with existing
`data-fetch` content.

### Decision 2: `validate_bar` returns a `list[IntegrityViolation]`

A list (not a single `Optional[IntegrityViolation]`) so a single bar
can fail multiple rules in one call. The validator collects *all*
violations; the caller decides what to do (log individually, drop the
whole bar, etc.).

`IntegrityViolation` carries a stable `Rule` enum code
(`high-below-o-h-l-c`, `volume-negative`, …). The codes are used in
`ingestion_logs.message` and in operator grep workflows.

`validate_bar` accepts both `dict` (SDK-style) and dataclass objects
(some ingest paths hand us SDK `Candle` objects, not dicts). A small
`_get(c, key)` helper hides the difference via `Mapping.get` /
`getattr`.

### Decision 3: `BAR_CORRUPTION` penalty = -40

| Existing issue                | Penalty |
|-------------------------------|--------:|
| `MISSING_RECENT`              |     30  |
| `SPARSE_HISTORY`              |     20  |
| `HAS_GAPS`                    |     20  |
| `RATE_LIMITED_FAILURES`       |     30  |
| `INCOMPLETE_HISTORY`          |     25  |
| **`BAR_CORRUPTION`** (new)    |   **40** |

40 is the largest single penalty because corruption is the worst
class of problem — a gap can be backfilled; corrupt data corrupts the
backtest. The remaining 60-point headroom means a figi with one
corruption issue can still be considered somewhat trustworthy.

### Decision 4: `EXP(SUM(LN(factor)))` instead of `PRODUCT()`

SQLite added `PRODUCT()` aggregate in 3.35. The project's target
SQLite is older. `EXP(SUM(LN(factor)))` works on every SQLite version
the project ships and computes the same value for positive factors
(all split factors are). Mitigation against zero factor: not needed
(CHECK constraint on action_type + numeric factors upstream).

### Decision 5: Validation runs *before* `replace_bars_for_figi`

We don't write partial data and then validate. The validator runs
per-candle inside the same loop that builds the `clean[]` list,
then `replace_bars_for_figi(db, figi, clean, replace=True)` writes
only the survivors in a single transaction. This means a single bad
candle does not poison a whole batch.

The `ingestion_logs` insert uses `level='warn'` (not `'error'`)
because the candle is dropped, not written — the run as a whole still
succeeds.

### Decision 6: Operator-maintained JSON for corporate actions

Same pattern as `moex_holidays.json`: a static JSON file in
`apps/api/scripts/data/corporate_actions.json` and an idempotent
loader. The plan acknowledges this is a manual-maintenance trade-off
and lists it as a known follow-up.

## Risks / Trade-offs

- **First-run shock.** Pre-existing corrupt rows in `bars` will all
  surface as `BAR_CORRUPTION` on the first health pass. Operationally
  expected; documented in the runbook.
- **Backfill fixture cleanup.** Tests in `test_backfill.py`,
  `test_bars_sqlite.py`, and possibly `test_data_reads_*` use fixture
  bars that violate at least one rule. Each must be sanitised to
  keep tests green after `validate_bar` is wired up.
- **`bars_adjusted` correlated subquery.** With 2.4 M bars × ~1 split
  per figi the cost is acceptable (~10 ms per query), but if
  `/api/bars/<symbol>` slows we will cache `adj_close` in a real
  column. Out of scope here.
- **Manual JSON.** Splits/dividends for new figis need a hand-merge.
  Acceptable MVP.
- **Dividend events are not folded into `adj_close` in this change.**
  This matches what most backtests already want; total-return is a
  separate follow-up.
