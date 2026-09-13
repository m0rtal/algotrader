# Tasks

## 1. Spec delta

- [ ] 1.1 Create `specs/frontend-shell/spec.md` (delta) with
  `## ADDED Requirements` header.
- [ ] 1.2 Add `Data tab merges Storage and Backfill` requirement
  with three scenarios (status header reads total_bars once,
  pending + controls remain, table row click opens drilldown).
- [ ] 1.3 `openspec validate merge-data-tab --strict` passes.
- [ ] 1.4 Apply delta to canonical `openspec/specs/frontend-shell/spec.md`
  (drop `(delta)` from title, rename `## ADDED Requirements` →
  `## Requirements`, ensure `## Purpose` exists above).
- [ ] 1.5 `openspec validate frontend-shell --strict` passes.
- [ ] 1.6 `openspec archive merge-data-tab --yes --skip-specs`.

## 2. TDD — write tests first

- [ ] 2.1 Create `apps/web/src/features/data/DataTab.test.tsx`
  with the existing fixtures (`defaultPending`, `defaultStatus`,
  `sampleTickers`) imported from the test setup.
- [ ] 2.2 RED: assert the merged page renders the status header
  cards with `total_bars` from `useBackfillStatus` (not from
  re-aggregated ticker data). Run, watch it fail.
- [ ] 2.3 RED: assert pending counters render with 4 buckets
  (new, stale, up_to_date, error). Run, watch fail.
- [ ] 2.4 RED: assert 3 buttons (Start, Stop, Reset). Run, watch fail.
- [ ] 2.5 RED: assert progress bar appears only when
  `tickers_total > 0`. Run, watch fail.
- [ ] 2.6 RED: assert ticker table renders with rows from
  `useTickers`, click on row calls `openTicker(symbol)`. Run, watch fail.
- [ ] 2.7 RED: assert removing the `storage` and `backfill` tabs
  from Dashboard leaves only the 5 expected tabs (Сигналы,
  Сделки, Портфель, Бэктест, Данные). Run, watch fail.

## 3. GREEN — implement DataTab

- [ ] 3.1 Create `apps/web/src/features/data/DataTab.tsx` with
  the layout from `design.md`.
- [ ] 3.2 Use `useBackfillStatus().total_bars` directly for the
  "Баров на диске" card (no `data.reduce`).
- [ ] 3.3 Reuse `useTickers()` for the table; per-row click →
  `useUiStore.openTicker(symbol)`.
- [ ] 3.4 Reuse `useStartBackfill` / `useStopBackfill` /
  `useForceReset` mutations; reuse `<ConfirmDialog danger …>` for
  reset.
- [ ] 3.5 Progress bar block conditionally rendered when
  `status.data?.tickers_total > 0`.

## 4. Wire Dashboard + delete old tabs

- [ ] 4.1 Update `apps/web/src/pages/Dashboard.tsx`: import
  `DataTab`, replace `storage` and `backfill` entries with a
  single `data` entry labelled «Данные».
- [ ] 4.2 Delete `apps/web/src/features/storage/StorageTab.tsx`
  and `apps/web/src/features/storage/` directory if empty.
- [ ] 4.3 Delete `apps/web/src/features/backfill/BackfillTab.tsx`.
  Keep `apps/web/src/features/backfill/BackfillTab.test.tsx`
  initially, then delete after merging tests into
  `DataTab.test.tsx`.

## 5. Verify

- [ ] 5.1 `pnpm typecheck` clean.
- [ ] 5.2 `pnpm lint:a11y` (token + contrast) green.
- [ ] 5.3 `pnpm vitest run src/features/data src/pages src/components/layout`
  green.
- [ ] 5.4 Manual smoke: open `localhost:5173`, switch to Данные,
  confirm status header / queue / table render without duplicated
  metrics.
