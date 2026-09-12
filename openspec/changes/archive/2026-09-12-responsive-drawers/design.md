# Design — responsive-drawers

## Stack

Same as `ui-shell` and `broker-section-redesign`: React 19 + Tailwind 4,
zustand for UI state, no new dependencies.

## Layout

```
Dashboard (h-screen flex flex-col)
├── Topbar                          [h-12, full width]
│   ├── ☰ toggle (lg:hidden) ──── aria-controls="dashboard-sidebar"
│   ├── ALGOTRADER · MOEX badge
│   └── IMOEX · ⚙ (lg:hidden) ── aria-controls="dashboard-rail"
│                                              ⚙ /settings link (lg+ only)
├── KPIStr                          [grid 2/3/6 cols]
│   └── 6 tiles, px-3 sm:px-[18px]
├── main grid                       [1 col <lg, 240|1fr|320 >=lg]
│   ├── <aside hidden lg:block> ← Sidebar (regime + universe)
│   ├── <main>
│   │   ├── Tab strip (overflow-x-auto)
│   │   └── Tab content (Signals/Trades/Portfolio/Backtest/Storage/Backfill)
│   └── <aside hidden lg:block> ← RightRail (model + features + pipeline)
├── <SidebarDrawer />   fixed overlay, <lg only, mount always
│   └── if (sidebarOpen) <div role="presentation"> <aside id="dashboard-sidebar"> <Sidebar/> </aside> </div>
├── <RightRailDrawer />  fixed overlay, <lg only, mount always
│   └── if (railOpen)    <div role="presentation"> <aside id="dashboard-rail">     <RightRail/> </aside> </div>
└── <TickerDrilldown />
```

## Data Flow

uiStore additions:

```
sidebarOpen: boolean           default false
railOpen:    boolean           default false

toggleSidebar()                flips sidebarOpen
toggleRail()                   flips railOpen
closeSidebars()                both false
openTicker(symbol)              sets selectedTicker, clears both drawers
```

On `<lg`, the Topbar's hamburger toggles `sidebarOpen` → `<SidebarDrawer>`
mounts the inner panel. On the same screen, clicking a ticker button
inside the drawer calls `openTicker(symbol)` → both drawers close
(`openTicker` resets them) → `<TickerDrilldown>` renders full-screen.

On `lg+`, the toggle buttons are `lg:hidden` and the drawers remain
`null`. The static columns already render the content.

## Accessibility

- Each toggle button has `aria-label` ("Открыть панель вселенной" /
  "Открыть панель модели") and `aria-controls` pointing at the
  drawer `id`. `aria-expanded` is not used here because the drawer
  itself is not mounted when closed — the screen reader reads the
  button label and the controlled region only exists when relevant.
- Drawer backdrop carries `role="presentation"` (decorative). The
  inner `<aside>` carries `aria-label`. Same pattern as
  ConfirmDialog and TickerDrilldown.
- Escape closes any open drawer via a `window` keydown listener
  installed inside `useEffect` with proper cleanup.
- Backdrop click: `e.target === e.currentTarget` guard prevents
  dismissing when the click bubbles up from the panel content.

## Risks

- **Modal stacking:** if a future change opens two modals/drawers
  at once, z-index ordering needs a defined convention. Currently
  `<TickerDrilldown>` and the two drawers are mutually exclusive
  (opening a drilldown closes the drawers).
- **MSW passthrough parity:** verified — `/api/regime`,
  `/api/tickers`, `/api/model`, `/api/model/features`,
  `/api/pipeline` are all in `apps/web/src/mocks/handlers.ts` and
  unchanged.
- **Reduced-motion:** no slide animation is added. Global
  `prefers-reduced-motion` rule in `ui-shell` already disables
  non-essential motion; future slide-in transitions can opt in
  with `motion-safe:` prefix when a real usability signal appears.

## Tests

- `uiStore.test.ts`: +6 tests covering `sidebarOpen` initial
  state, `toggleSidebar`, `toggleRail`, `closeSidebars`, and
  `openTicker` resetting both drawers.
- Layout tests: existing `Topbar.test.tsx`, `Sidebar.test.tsx`,
  `RightRail.test.tsx`, `KPIStr.test.tsx`, `LogStrip.test.tsx`
  cover the static-column behaviour and continue to pass — they
  do not need to grow because the drawer's mount-only-when-open
  pattern means DOM snapshots are identical to the pre-change
  baseline when both `sidebarOpen` and `railOpen` are false.
- The `Dashboard.test.tsx` suite has a pre-existing hang on the
  Portfolio tab assertion (verified on `HEAD` without this change's
  diffs). Out of scope for this change; tracked separately.
