# Tasks

## 1. Spec delta

- [ ] 1.1 Create `specs/frontend-shell/spec.md` (delta) with
      `## ADDED Requirements` header.
- [ ] 1.2 Add `Responsive drawer panels` requirement with three
      scenarios (sidebar toggle, rail toggle, ticker-closes-drawers).
- [ ] 1.3 `openspec validate responsive-drawers --strict` passes.
- [ ] 1.4 Apply delta to canonical `openspec/specs/frontend-shell/spec.md`
      (drop `(delta)`, rename `## ADDED Requirements` → `## Requirements`,
      ensure `## Purpose` exists).
- [ ] 1.5 `openspec validate frontend-shell --strict` passes.
- [ ] 1.6 `openspec archive responsive-drawers --yes --skip-specs`.

## 2. uiStore state

- [ ] 2.1 Add `sidebarOpen`, `railOpen` booleans to `UiState`.
- [ ] 2.2 Add `toggleSidebar`, `toggleRail`, `closeSidebars` actions.
- [ ] 2.3 `openTicker` resets both drawers.
- [ ] 2.4 Tests in `uiStore.test.ts`: initial state, toggles,
      `closeSidebars`, `openTicker` resets — all green.

## 3. SidebarDrawer + RightRailDrawer

- [ ] 3.1 `<SidebarDrawer>`: fixed overlay, `lg:hidden`, backdrop
      click closes, Escape closes via window listener with cleanup,
      inner `<aside id="dashboard-sidebar" aria-label="Панель вселенной">`
      contains `<Sidebar />`.
- [ ] 3.2 `<RightRailDrawer>`: mirror pattern, slides from the
      right, `id="dashboard-rail"`, `<RightRail />` inside.
- [ ] 3.3 jsx-a11y: backdrop click guarded by `eslint-disable`
      with same rationale as ConfirmDialog/TickerDrilldown.

## 4. Topbar toggles

- [ ] 4.1 Hamburger ☰ button, `lg:hidden`, `aria-label`,
      `aria-controls="dashboard-sidebar"`, `onClick={toggleSidebar}`.
- [ ] 4.2 Gear ⚙ button, `lg:hidden`, `aria-controls="dashboard-rail"`,
      `onClick={toggleRail}`.
- [ ] 4.3 Gear `/settings` link retained, `hidden lg:inline`.

## 5. Dashboard mount + layout

- [ ] 5.1 Import `<SidebarDrawer>` and `<RightRailDrawer>`.
- [ ] 5.2 Mount them as siblings of `<TickerDrilldown />` inside
      the Dashboard root.

## 6. Mobile polish

- [ ] 6.1 `KPIStr` tile padding: `px-3 sm:px-[18px]`.
- [ ] 6.2 `TickerDrilldown` modal: `rounded-none sm:rounded-lg`,
      `max-w-full sm:max-w-[640px] md:max-w-[800px]`,
      `max-h-screen sm:max-h-[80vh]`.

## 7. Validation

- [ ] 7.1 `pnpm typecheck` clean.
- [ ] 7.2 `pnpm lint:a11y` (token + contrast) green.
- [ ] 7.3 `pnpm vitest run src/stores src/components/layout src/components/charts src/features/settings`
      green.
- [ ] 7.4 Manual walkthrough at 360px viewport — open sidebar, click
      ticker, drawer closes, drilldown full-screen, back.
- [ ] 7.5 Manual walkthrough at 1440px viewport — toggles hidden,
      columns visible, no regressions.
