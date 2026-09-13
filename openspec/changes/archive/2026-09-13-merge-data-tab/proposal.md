# Proposal: merge-data-tab

## Why

The Dashboard shell currently exposes two adjacent tabs that ask
overlapping questions about the same underlying data:

- **Бары** (`StorageTab`) — read-only inventory: total bars on
  disk, period covered, completeness percentage, table by ticker
  with bars / size / period / gaps.
- **Бэкфил** (`BackfillTab`) — write/control: scheduler status,
  pending bucket counts (new / stale / error), progress bar,
  Start / Stop / Reset metadata buttons.

Both tabs surface `total_bars` and ticker counts. The operator who
opens one to ask "is the data fresh?" has to switch to the other
to ask "what's the queue doing?" — a friction that costs a tab
switch and a context rebuild for every routine check. The two
screens also duplicate the metric card cluster at the top of
each tab.

This change merges the two tabs into a single **Данные** tab that
keeps both intents and arranges them vertically: status at the
top (read), then the queue + controls (write), then the per-ticker
table (inventory). No metric appears in two places; the
`total_bars` / `tickers_count` aggregates are read once from
`useBackfillStatus()` and reused across the page.

## What Changes

- Remove the `storage` and `backfill` tabs from the Dashboard
  Tab strip and add a `data` tab labelled «Данные».
- Create a new `DataTab` component under
  `apps/web/src/features/data/DataTab.tsx` that composes:
  - A status header section (scheduler state, total bars on disk,
    period, completeness, gaps) sourced from
    `useBackfillStatus()` and `useTickers()`.
  - A backfill queue section (pending bucket counts, progress bar
    while running, Start / Stop / Reset buttons) sourced from
    `usePendingCount()` and the existing `useStartBackfill` /
    `useStopBackfill` / `useForceReset` mutations.
  - A ticker table (figi / name / sector / bars / size / period /
    gaps) sourced from `useTickers()`. Clicking a row opens
    `<TickerDrilldown>` via `openTicker(symbol)`.
- Delete `apps/web/src/features/storage/StorageTab.tsx` and
  `apps/web/src/features/backfill/BackfillTab.tsx` once `DataTab`
  ships. The reusable hooks (`useBackfillStatus`, `useStartBackfill`,
  `useStopBackfill`, `usePendingCount`, `useForceReset`, `useTickers`)
  stay in `apps/web/src/lib/hooks.ts` and are reused unchanged.
- Drop the storage and backfill MSW handlers from
  `apps/web/src/mocks/handlers.ts` if they exist as a passthrough
  pair that only the merged tabs need. The passthrough for the live
  backend (`VITE_API_BASE_URL=http://127.0.0.1:8000/api`) keeps
  working — the endpoints themselves don't move.
- Tab strip renders 5 tabs (Сигналы, Сделки, Портфель, Бэктест,
  Данные) instead of 6.

## Impact

- Affected specs: `frontend-shell` (one ADDED requirement).
- Affected code: `apps/web/src/pages/Dashboard.tsx` (tab list),
  `apps/web/src/features/storage/StorageTab.tsx` (deleted),
  `apps/web/src/features/backfill/BackfillTab.tsx` (deleted),
  `apps/web/src/features/data/DataTab.tsx` (new),
  `apps/web/src/mocks/handlers.ts` (passthrough cleanup if
  applicable), `apps/web/src/features/backfill/BackfillTab.test.tsx`
  + `apps/web/src/features/storage/StorageTab.test.tsx` (deleted),
  `apps/web/src/features/data/DataTab.test.tsx` (new).
- No backend change. All four endpoints (`/api/tickers`,
  `/api/admin/backfill/status`, `/api/admin/backfill/pending`,
  `/api/admin/backfill/{start,stop,force-reset}`) already exist.
- No `packages/shared` change.
- No type definitions added — `BackfillStatus`, `Pending`,
  `Ticker` types move from `BackfillTab.tsx` to `DataTab.tsx`
  or `lib/hooks.ts`.
- No new dependencies.

## Non-Goals

- No backend endpoint changes.
- No changes to how the scheduler runs (daily 02:00 MSK stays).
- No keyboard shortcuts for tab navigation (out of scope).
- No light theme work.
- No mobile-only layout — DataTab uses the same vertical layout
  as the current backfill tab (full-width sections, no grid-
  columns) so it already collapses gracefully.

## Risks

1. **Test surface migration.** The existing 18 BackfillTab tests
   and 5 StorageTab tests will be replaced by a new
   `DataTab.test.tsx` suite. The mock fixtures (`defaultPending`,
   `defaultStatus`, `sampleTickers`) carry over verbatim. Tests
   that asserted isolated behaviour (`renders idle state`,
   `subscribes to EventSource on mount`) become part of an
   integrated suite asserting the merged flow.
2. **`<h2>` heading duplication.** StorageTab has «Бары»;
   BackfillTab has «Бэкфилл». The merged page has «Данные»;
   internal sections use `<h3>` to keep the heading hierarchy
   correct.
3. **`aria-live` region.** The progress bar block was already
   `aria-live="polite"`; preserved verbatim in the merged
   section so screen-reader announcements don't regress.
4. **MSW handlers must remain.** Each endpoint already has its
   passthrough; merging the tabs does not delete any. The
   `mocks/handlers.ts` only changes if there were storage-only
   or backfill-only fixtures that no longer apply.

## Done means

- [ ] Spec delta exists at `openspec/changes/merge-data-tab/specs/frontend-shell/spec.md`
- [ ] All 4 supporting files (proposal, design, tasks, spec) exist
- [ ] `openspec validate merge-data-tab --strict` passes
- [ ] Canonical `frontend-shell` updated, `openspec validate frontend-shell --strict` passes
- [ ] `openspec archive merge-data-tab --yes --skip-specs`
- [ ] Tests pass: typecheck + lint:a11y + vitest on the DataTab file
- [ ] Manual smoke test: Dashboard renders 5 tabs, the Данные tab
  shows status + queue + table without duplicated metrics.
- [ ] PR opened against `main` (per AGENTS.md).
