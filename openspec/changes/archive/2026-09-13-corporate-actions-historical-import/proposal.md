# corporate-actions-historical-import

## Why

The `corporate_actions` table was added by the `data-quality-integrity`
change with seven demo events and two hand-curated "real figi mirror"
rows (BBG000BPH459, BBG004731032). The `bars_adjusted` view exists and
is wired into the `INCOMPLETE_HISTORY` health check, but the
adjustment factor is `1.0` for every real figi in production — so
`adj_close == close` for the 3775 tradeable instruments and the
adjusted-close benefit is theoretical, not practical.

Without real data, the operator cannot answer "how complete is the
historical bar series for SBER?" with anything more accurate than the
raw bar count. To make the `INCOMPLETE_HISTORY` check meaningful, the
`corporate_actions` table needs to carry real split and dividend
events for every figi in the universe — historical (so backtests
survive past splits without misreading them as crashes) and ongoing
(so the operator can detect new events as they happen).

The pre-change state is "the schema is ready but empty for production
data." The post-change state is "the table has curated historical
splits for every Russian/global figi, plus all dividends Tinkoff
sandbox can return, plus all dividends MOEX ISS can return — three
sources merged into one row each via a single common writer."

## What Changes

1. **`scripts_import/import_corporate_actions_common.py`** — NEW. Shared
   `CorporateActionRow` dataclass (validates `action_type ∈
   {'split','dividend'}` and `factor > 0`) and
   `merge_into_corporate_actions(db_path, rows)` helper using
   `INSERT OR REPLACE` on the existing primary key `(figi,
   action_type, ex_date)`. All three importers below call this helper.

2. **`scripts/data/splits_curated.json`** — NEW. 17 historical splits for
   Russian and global paper (SBER 2020-06-19 2:1, GAZP 2021-07-30
   10:1, YNDX 2014-06-18 4:1, LKOH 2014-06-11 2:1, MSFT 2022-08-31,
   AAPL 2014-06-09 7:1, GOOGL 2022-06-09 20:1, TSLA 2020-08-31,
   AMZN 2010-01-21, NLMK 2019-04-19, MAGN 2017-06-27, ROSN 2011-04-15,
   VTBR 2011-11-15, MGNT 2014-06-16, NVTK 2014-06-19, MTSS 2014-06-16).
   Covers all figis known to have had at least one split; the bootstrap
   that no free API provides.

3. **`scripts_import/import_splits_curated.py`** — NEW. Loads the
   curated JSON and merges rows via the common writer.
   Idempotent.

4. **`scripts_import/import_corporate_actions_tinkoff.py`** — NEW.
   Iterates the tradeable figis in `instruments` and calls
   `client.instruments.get_dividends(figi, from_, to)` for each.
   Converts each Tinkoff `Dividend` event to a
   `CorporateActionRow(action_type='dividend', cash_amount=<units +
   nano/1e9>, factor=1.0)`. Sandbox-friendly; production-token
   compatible.

5. **`scripts_import/import_corporate_actions_moex.py`** — NEW. Iterates
   the same figis, resolves each to a `secid` via `instruments.ticker`
   (MOEX paper: `ticker == secid`), and calls
   `GET https://iss.moex.com/iss/securities/{secid}/dividends.json`.
   Returns empty on network failure (does not abort the whole
   import); skips zero-value entries.

6. **Thin operator wrappers** at
   `apps/api/scripts/import_{splits_curated,corporate_actions_tinkoff,
   corporate_actions_moex}.py` mirror the existing
   `import_moex_holidays.py` pattern (delegate to the in-package
   module so both `python -m scripts.<name>` and the test path
   `algotrader_api.scripts_import.<name>` work).

7. **OpenSpec delta** — append a new Requirement to the existing
   `data-fetch` capability documenting the multi-source contract and
   the idempotent merge.

## Impact

- **Operator workflow**: run three operator scripts in sequence after
  merging to populate `corporate_actions`. The curated JSON import is
  instant; Tinkoff and MOEX ISS imports take 1-5 min each on the
  current universe. Re-runs are safe.
- **`bars_adjusted` view**: returns correct `adj_close` for every figi
  that has at least one split row. Pre-split bars are scaled DOWN by
  the cumulative factor; post-split bars are unchanged.
- **`INCOMPLETE_HISTORY` health check**: now meaningful because
  `expected_bars` is calculated against the holiday-aware weekday
  count, and `adj_close`-based ratio comparisons can be made if a
  future change wants them.
- **Tests**: +10 tests across 3 new test files. Coverage stays at or
  above the 95% floor.
- **No new dependencies** — `tinkoff.invest` is already installed; only
  stdlib `urllib` is needed for MOEX ISS HTTP calls.
- **No runtime impact** — these are operator scripts, not on the
  data-quality-guardian cron path. The daily cron still does freshness
  checks; this is a one-shot bulk historical loader.

## Non-Goals

- **Auto-detection of future splits via `face_value`/`lot` diffing**.
  Tinkoff has no native splits feed, and MOEX ISS only gives current
  snapshots, so ongoing detection needs a local snapshot store
  against which to diff. That's a separate change.
- **Currency-aware dividend totals** — the schema has `cash_amount
  REAL` with no currency, mixing RUB and USD silently. Out of scope
  here; documented as a known limitation in the spec.
- **Bond coupon events** — schema `CHECK` constrains `action_type` to
  `('split', 'dividend')`. Adding `coupon` requires a migration.
- **Total-return / dividend-reinvested close view** — separate
  feature, separate change.
- **Reconciliation between sources** — when Tinkoff and MOEX ISS
  disagree on the same dividend event, this change keeps the last
  writer's row. A reconciliation job is out of scope.

## Risks

- **Network failure modes in Tinkoff/MOEX importers.** Both wrappers
  return empty results on exception rather than aborting the whole
  import. The operator gets `Imported 0 dividends` and can re-run.
  The data-quality cron does not depend on these importers, so a
  transient network failure does not break the live system.
- **MOEX `secid` mapping is approximate.** We assume `instruments.ticker
  == secid`. For MOEX-listed paper this is true; for US/global paper
  it is not (and MOEX ISS won't have them anyway). The importer
  silently skips figis for which no `secid` resolves. A future change
  can add a dedicated secid column.
- **Tinkoff sandbox data depth.** The sandbox may return only a few
  years of dividend history, or none for figis not present in the
  test instrument universe. The fetcher is correct; the data
  quality depends on what the broker returns. A production token
  yields longer history.
- **No machine-readable provenance.** The curated JSON has a `note`
  string but no URL/source field. Acceptable for the bootstrap; a
  future change can add a `source` column if cross-source audit
  becomes important.
- **Pre-commit hook.** `prettier` rewrites the curated JSON on every
  commit. Acceptable; the JSON is single-line per entry which is
  valid JSON either way.
