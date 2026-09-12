# frontend-shell Specification (delta)

## ADDED Requirements

### Requirement: Responsive drawer panels

The Dashboard shell SHALL expose the Sidebar and RightRail content
to mobile viewports (`<lg`, < 1024px) through fixed-overlay drawers
that mirror the desktop static columns. The drawers SHALL be
controlled by transient booleans in `useUiStore` and SHALL be
keyboards-accessible (Escape closes, backdrop click closes).

#### Scenario: hamburger opens the sidebar drawer

- **GIVEN** the viewport is `<lg`
  **AND** the operator is on `/`
- **WHEN** the operator clicks the hamburger button in the Topbar
- **THEN** the `sidebarOpen` boolean becomes `true`
- **AND** a fixed overlay renders with the Sidebar content inside an
  `<aside id="dashboard-sidebar" aria-label="Панель вселенной">`.

#### Scenario: gear opens the right rail drawer

- **GIVEN** the viewport is `<lg`
  **AND** the operator is on `/`
- **WHEN** the operator clicks the gear button in the Topbar
- **THEN** the `railOpen` boolean becomes `true`
- **AND** a fixed overlay renders with the RightRail content inside
  an `<aside id="dashboard-rail" aria-label="Панель модели">`.

#### Scenario: opening a ticker closes both drawers

- **GIVEN** either `sidebarOpen` or `railOpen` is `true`
- **WHEN** the operator clicks a ticker button (Sidebar universe list)
- **THEN** both `sidebarOpen` and `railOpen` become `false`
- **AND** the `selectedTicker` becomes the clicked symbol
- **AND** `<TickerDrilldown>` opens full-screen on `<sm`.

#### Scenario: Escape closes an open drawer

- **GIVEN** `sidebarOpen` is `true`
- **WHEN** the operator presses `Escape`
- **THEN** `sidebarOpen` becomes `false`.

#### Scenario: backdrop click closes the drawer

- **GIVEN** `sidebarOpen` is `true`
- **WHEN** the operator clicks the backdrop (outside the inner
  `<aside>`)
- **THEN** `sidebarOpen` becomes `false`
- **AND** clicking inside the `<aside>` (panel content) does not
  close the drawer.
