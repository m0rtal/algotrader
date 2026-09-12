# ui-shell Specification (delta)

## Purpose

The frontend shell — top bar, KPI strip, sidebars, tab navigation,
log strip, modal scaffold, and base accessibility/contrast guarantees
that apply to every screen in the app.

## ADDED Requirements

### Requirement: Token integrity

The shell SHALL declare every CSS custom property referenced in
component code as either a `@theme {}` token or an explicit alias.
Undeclared references SHALL fail a lint check (`scripts/token-check.mjs`).

#### Scenario: Missing var alias

- **WHEN** a component references `var(--card)` and `--card` is not
  declared in `styles/globals.css`
- **THEN** the build fails with a path to the offending file and line.

### Requirement: Color contrast

All text-on-background and UI-control-on-background pairs SHALL meet
WCAG 2.2 AA at the body text size (≥4.5:1 for text, ≥3:1 for borders
and large text).

#### Scenario: Bumped accent token

- **GIVEN** `--color-accent` is set to `#7a82ff`
- **WHEN** measured against `var(--color-bg)` (`#0b0e14`)
- **THEN** contrast ratio is ≥ 4.5:1.

### Requirement: Keyboard focus visibility

Every interactive element (`button`, `a`, `[role="button"]`,
`[role="tab"]`, form controls) SHALL show a visible focus indicator
when focused via keyboard.

The focus indicator SHALL be a 2px solid ring using `--color-accent`
with 2px offset.

#### Scenario: Focus ring on button

- **GIVEN** a `<button>` is rendered
- **WHEN** the user tabs to it
- **THEN** the button shows the accent-coloured focus ring.

### Requirement: Button type hygiene

Every `<button>` not used as a form submitter SHALL set
`type="button"`. The shell SHALL enforce this via ESLint custom rule
or a grep-based check in CI.

#### Scenario: Tab nav button

- **GIVEN** the dashboard tab strip renders 6 `<button>` elements
- **WHEN** `grep -E '<button\b' apps/web/src/pages/Dashboard.tsx` is run
- **THEN** every match includes `type="button"`.

### Requirement: Modal semantics

Modal dialogs (`TickerDrilldown`, `ConfirmDialog`, future modals)
SHALL expose:

- `role="dialog"`
- `aria-modal="true"`
- `aria-labelledby` pointing at the visible title
- Escape closes the dialog
- Focus is moved into the dialog on open and trapped while open
- Focus is restored to the trigger on close

#### Scenario: ConfirmDialog opens via Reset button

- **GIVEN** the user clicks "Reset metadata" on the Backfill tab
- **WHEN** the dialog mounts
- **THEN** the panel has `role="dialog" aria-modal="true"` and the
  title is linked via `aria-labelledby`; pressing Escape closes the
  dialog and returns focus to the Reset button.

### Requirement: Toast announcement

Toasts SHALL be announced to assistive technology by setting
`role="status"` AND `aria-live="polite"` AND `aria-atomic="true"`.

#### Scenario: Settings save success toast

- **GIVEN** the user saves settings successfully
- **WHEN** the toast appears with message "Сохранено"
- **THEN** a screen reader announces the message on next idle.

### Requirement: Section headings

Section labels rendered in uppercase tracked typography SHALL be
semantic headings (`<h2>` / `<h3>`) so screen-reader heading
navigation works.

#### Scenario: Sidebar regime panel

- **GIVEN** the Sidebar renders the "Режим рынка" header
- **WHEN** the document outline is read by assistive technology
- **THEN** "Режим рынка" appears as an `<h3>` and is reachable via
  the heading shortcut.

### Requirement: Loading and empty states

Every data-driven tab SHALL render exactly one of: loading skeleton,
error message with retry, empty-state message, or the populated view.
The bare default render of "nothing" for an empty dataset is forbidden.

#### Scenario: Trades tab with no trades

- **GIVEN** `/api/trades` returns `[]`
- **WHEN** the user opens the Trades tab
- **THEN** an empty-state message is visible (not a blank panel).

### Requirement: Motion sensitivity

Decorative infinite animations (e.g. status pulse) SHALL be suppressed
when the user has set `prefers-reduced-motion: reduce`.

#### Scenario: Topbar status pulse under reduced motion

- **GIVEN** the user has `prefers-reduced-motion: reduce` enabled
- **WHEN** the dashboard mounts with the live status indicator
- **THEN** the indicator's `animate-pulse` animation does not run.

### Requirement: Live log strip

The global log strip SHALL show a skeleton placeholder while the first
fetch is in flight, instead of rendering nothing.

#### Scenario: Cold load of LogStrip

- **GIVEN** the user lands on `/` and `/api/logs` has not yet
  responded
- **WHEN** the LogStrip mounts
- **THEN** the strip is in the DOM with `data-testid="global-log-strip"`
  and shows the placeholder text "— Подключение к логам… —".
