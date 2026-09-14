# corporate-actions-m-oex-iss-source-of-truth

## Why

The previous change `corporate-actions-historical-import` shipped a
`splits_curated.json` file containing hand-typed corporate-action
rows. This is **unacceptable** for a project that trades live money:

- The factor values are partially fabricated. The 1998 1003:1 split
  for VTBR has no source; the 2011 5:1 split for VTBR is *inferred*
  from the 2024 reverse-split math, not from any primary source.
- The 2024 5000:1 reverse split for VTBR is verified by news articles
  but **missing from the curated JSON** — it would never have been
  applied to historical bars.
- A single bad factor on a single figi would silently corrupt every
  backtest that touches that figi. The data is in production today
  (committed via PR #12 merge).

The pre-change state is "an unauditable JSON file is treated as
ground truth." The post-change state is "every row in
`corporate_actions` has a `source` field pointing at a primary,
machine-verifiable URL; rows without a source are not loaded."

## What Changes

1. **`corporate_actions` table gets a `source` column** (TEXT, nullable).
   New migration `009_corporate_actions_source.sql`. Existing rows are
   backfilled: any row whose `note` starts with `tinkoff:` or
   `moex:iss` is backfilled with that prefix; everything else
   (curated FIGI-* demo rows, the hand-typed "real figi mirror" rows,
   and any unannotated rows) is **deleted**.

2. **`splits_curated.json` and its importer are removed.**
   `import_splits_curated.py` and `apps/api/scripts/import_splits_curated.py`
   are deleted. The `data/splits_curated.json` file is deleted.

3. **New `scripts/import_corporate_actions_splits.py`** — periodic
   `face_value`/`lot` snapshot diffing. On every run, fetches
   `/iss/securities/{secid}.json` for every tradeable figi, writes
   the (figi, `face_value`, `lot`, `observed_at`) tuple into a new
   `instruments_snapshot` table. A scheduled operator script compares
   consecutive snapshots and writes a `split` row into
   `corporate_actions` whenever `face_value` or `lot` changes between
   snapshots. The split's `factor` is `face_value_post / face_value_pre`
   (signed to support both forward and reverse splits). `source` =
   `moex_iss:split-diff:<snapshot_before>:<snapshot_after>`.

4. **`scripts/import_corporate_actions_moex.py`** is hardened. Each
   fetched dividend row carries `source = 'moex:iss:dividends'`. Rows
   are not deduplicated by `(figi, ex_date, value)` alone — only by
   `(figi, action_type, ex_date)` plus an exact amount match, so that
   Tinkoff-vs-MOEX disagreements remain visible as two separate rows
   (or are excluded if the amounts disagree beyond a tolerance — see
   scenario below).

5. **Tinkoff dividends fetcher is wired up to live mode** (the
   previous change shipped it with a `# pragma: no cover` runtime
   stub). It now uses the same `AsyncClient.__aenter__` pattern as
   `ingestion/real_client.py`, reads the broker token from the
   `secrets` table, and writes `source = 'tinkoff:dividends'`. The
   runtime call is gated by `ALGOTRADER_TINKOFF_ENABLE` env var
   (default off) to keep it out of the cron path until it has been
   live-tested.

6. **OpenSpec delta** — `data-fetch` capability gains a new
   requirement: "every `corporate_actions` row MUST carry a `source`
   field that points at a primary, machine-verifiable URL; rows
   without such a source SHALL NOT be loaded."

## Impact

- **`corporate_actions` table loses 18 rows in production** (7 demo
  FIGI-* rows, 2 hand-typed "real figi mirror" rows, 9 curated
  splits that have no verifiable source). The remaining rows after
  this change are only those written by `tinkoff:dividends` or
  `moex:iss:dividends`.
- **`bars_adjusted` view** now returns `adj_close = close` for every
  figi until the split-detection snapshot-diff has accumulated two
  snapshots and detected a real split. This is the right
  behaviour — `bars_adjusted` should not apply an unauditable factor.
- **Live operators** run three scripts on a weekly cadence:
  `import_corporate_actions_splits.py` (writes snapshot),
  then later (after ≥2 snapshots exist) `import_corporate_actions_splits.py`
  with `--detect` to compute splits, and `import_corporate_actions_moex.py`
  for dividends. All three are idempotent and safe to re-run.
- **`backfill` tests** that previously seeded FIGI-* demo rows are
  updated to seed rows with the `source` field. Tests that depend on
  curated FIGI-* data are updated to use real figis (`BBG004730N88`,
  `BBG004731032`, etc.) and a documented mock source.

## Non-Goals

- **No reconciliation between Tinkoff and MOEX ISS values**. When the
  two sources disagree, both rows stay in the table with their
  respective `source` values, and `bars_adjusted` uses the
  conservative behaviour of "no factor applied" until the
  discrepancy is investigated. A future change may add a
  reconciliation job.
- **No support for preferred-share events on dual-listed figis**.
  VTBR-preferred and VTBR-common are two `instruments` rows; the
  2026 preferred→common conversion will be picked up by the
  snapshot-diff only if `face_value` of either class changes — which
  is not the case for a pure class-consolidation event. Out of scope
  here.
- **No US/global paper splits**. Tinkoff and MOEX ISS are the two
  sources for this change; both cover MOEX paper. US/global paper
  (MSFT, AAPL, etc.) is left for a follow-up that adds Massive or
  FMP.
- **No retroactive split detection from pre-existing bars**. A
  figi with bars from 2010 and a single 2020-06-19 split cannot be
  recovered without either (a) a pre-existing snapshot from before
  the split, or (b) a curated historical source we trust. The
  curated approach is rejected on principle; snapshot-diff only
  detects splits that occur *after* the first snapshot.

## Risks

- **First run of `import_corporate_actions_splits.py` writes a
  snapshot but no split rows** — by design. It takes at least one
  full interval (the operator decides: weekly / monthly) before the
  diff starts producing rows.
- **The 2024 VTBR reverse split** (already in production) cannot be
  retroactively captured by snapshot-diff because we have no
  pre-snapshot. The operator may need to enter it manually for
  legacy splits, but only via the same `source` audit mechanism — a
  manual row carries `source = 'manual:<operator_name>:<reason>'`
  and a comment explaining why no automated source was available.
  The first such manual row should land in an ADR that lists every
  known historical split for every tracked figi with the reason the
  automation couldn't capture it.
- **MOEX ISS rate limit** (~50 req/min) caps the snapshot-diff
  throughput. The cron cadence is set by the operator.
- **Currency-aware dividend totals are still out of scope**. The
  `cash_amount REAL` column remains currency-blind. A future change
  may add a `currency` column.

## Rollback

- The `source` column is nullable, so dropping it via a reverse
  migration is safe.
- The `splits_curated.json` deletion is reversible via `git revert`
  on this commit, but **should not be reverted** — the curated
  approach is rejected on principle.
- The new `import_corporate_actions_splits.py` script can be left
  unused without breaking anything (the cron job is operator-managed).

## Open questions for the operator

1. **Manual row schema**: do we want `source = 'manual:<operator_name>:<reason>'`
   rows for historical splits the snapshot-diff can't recover? My
   recommendation is yes, with an ADR documenting every such row.
2. **Tinkoff enable env var**: `ALGOTRADER_TINKOFF_ENABLE` defaults
   off. The operator flips it on after a successful live test of the
   async SDK wiring.
3. **Snapshot cadence**: weekly? monthly? The shorter the cadence,
   the faster new splits are detected, but the more rate-limit
   pressure on MOEX ISS.
