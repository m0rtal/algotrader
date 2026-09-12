# Proposal: responsive-drawers

## Why

The dashboard shell (`apps/web/src/pages/Dashboard.tsx`) breaks down
below the `lg` breakpoint (1024px). Two of its three main columns
collapse to `hidden lg:block`:

- `Sidebar` (240px) is hidden on `<lg`.
- `RightRail` (320px) is hidden on `<lg`.
- Only the central tab column + Tab strip remain.

A user on a phone or a small window therefore sees a Dashboard
stripped of regime context, universe list, ML model summary, and
pipeline status — the most information-dense parts of the
application. On a narrow window the tab strip overflows horizontally
without a visible affordance.

The KPI strip already collapses (2 cols → 3 cols → 6 cols), so the
crash below `lg` is solely the Sidebar/RightRail treatment.

## What Changes

- Add two transient booleans to `useUiStore`: `sidebarOpen`,
  `railOpen`, with `toggleSidebar`, `toggleRail`, `closeSidebars`
  actions. `openTicker` resets both (drawers close when a drill-down
  opens).
- New `<SidebarDrawer>` component (`Sidebar.tsx`) — fixed overlay on
  `<lg`, mounted always, returns `null` while closed. Backdrop click
  dismisses, `Escape` closes via `window` keydown listener with
  cleanup. Renders the existing `<Sidebar />` body inside an
  `<aside id="dashboard-sidebar">`.
- New `<RightRailDrawer>` component (`RightRail.tsx`) — same pattern,
  slides in from the right, `<aside id="dashboard-rail">`.
- Topbar (`Topbar.tsx`) gains two `<button type="button">` controls
  (☰ sidebar, ⚙ rail) visible only `lg:hidden`. The ⚙ link to
  `/settings` stays visible on `lg+` so desktop users keep the original
  affordance. Both buttons carry `aria-label` and `aria-controls`
  pointing at the drawer `id`s.
- `Dashboard.tsx` mounts `<SidebarDrawer />` and `<RightRailDrawer />`
  as siblings of `<TickerDrilldown />`. The static column layout
  (Sidebar/RightRail between `hidden lg:block`) is unchanged on
  `lg+`.
- `KPIStr.tsx` reduces horizontal padding on mobile
  (`px-3 sm:px-[18px]`) — narrow tiles had `px-[18px]` which left
  barely 4px between digits and the edge on a 360px viewport.
- `TickerDrilldown.tsx` modal goes full-screen on `<sm` (rounded
  corners removed, `max-w-full`, `max-h-screen`).

## Impact

- Affected specs: `frontend-shell` (one ADDED requirement).
- Affected code: `apps/web/src/stores/uiStore.ts`,
  `apps/web/src/components/layout/Topbar.tsx`,
  `apps/web/src/components/layout/Sidebar.tsx`,
  `apps/web/src/components/layout/RightRail.tsx`,
  `apps/web/src/components/layout/KPIStr.tsx`,
  `apps/web/src/pages/Dashboard.tsx`,
  `apps/web/src/components/charts/TickerDrilldown.tsx`,
  `apps/web/src/stores/uiStore.test.ts`.
- No backend change. No MSW change (existing passthrough already
  handles `/api/regime`, `/api/tickers`, `/api/model`,
  `/api/model/features`, `/api/pipeline`).
- No `packages/shared` change.
- No new dependencies.
- Type definitions unchanged.

## Non-Goals

- **Keyboard shortcuts** (`/` ticker search, `?` help). Not in scope.
- **TickerDrilldown stats grid mobile collapse** (currently 4 cols
  on `<sm`). Deferred — touch surfaces are wider than phone width
  for a trading tool; the modal itself already stretches full-screen.
- **Light theme adjustments** — separate `theming-light` capability.
- **Bottom-sheet variant for very small viewports** — drawer is the
  standard pattern for trading UIs; bottom-sheet can be revisited if
  the drawer fails mobile usability testing.
- **`<lg` breakpoint change.** Tailwind defaults stay.

## Risks

1. **Modal/drawer stacking with TickerDrilldown.** A user opens the
   sidebar, picks a ticker — drawer closes, drilldown opens. Tested
   in `uiStore.test.ts`. If a future flow opens a drawer AND a
   drilldown simultaneously, z-index ordering needs review.
2. **Backdrop click + content click.** The backdrop closes on
   `e.target === e.currentTarget`. Children inside the `<aside>`
   don't trigger the dismiss. Verified by `jsx-a11y/no-static-element-interactions`
   escape hatch with the same rationale used for ConfirmDialog and
   TickerDrilldown.
3. **Reduced-motion**: drawer slide is absent here (drawer appears
   instantly). Acceptable — the global `prefers-reduced-motion`
   rule from `frontend-shell` already disables non-essential motion;
   no extra transitions are introduced.
4. **MSW passthrough parity**: `/api/regime` and friends already
   mocked. Drawers call the same hooks as the static columns.
