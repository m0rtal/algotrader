# frontend-shell Specification (delta)

## ADDED Requirements

### Requirement: Data tab merges Storage and Backfill

The Dashboard shell SHALL expose a single **Данные** tab that
combines the read-only inventory previously shown on the «Бары»
tab and the write/control surface previously shown on the «Бэкфил»
tab into one vertically-scrolling page. The tab strip SHALL expose
five tabs (Сигналы, Сделки, Портфель, Бэктест, Данные) instead
of six.

The merged page SHALL arrange its contents in three sections,
top to bottom: system state (read), backfill queue (write), and
per-ticker inventory (table). It SHALL NOT render any metric in
two places — for example, "total bars" is read once from the
backfill status endpoint and reused on the status header rather
than re-aggregated from the ticker list.

#### Scenario: Status header reads total_bars once

- **GIVEN** the operator is on the Данные tab
- **AND** `useBackfillStatus()` returns `total_bars = 2_361_394`
- **WHEN** the page renders
- **THEN** the «Баров на диске» card shows `2 361 394`
- **AND** the per-ticker table does NOT show a separate total-bars
  card or aggregate row that duplicates that value.

#### Scenario: Queue + controls remain after the merge

- **GIVEN** the operator is on the Данные tab
- **AND** scheduler state is `idle`
- **WHEN** the page renders
- **THEN** three buttons are visible: «Запустить бэкфилл»,
  «Остановить», «Сбросить метаданные».
- **AND** the pending counters (Новые / Устаревшие / С ошибками)
  render with values from `usePendingCount()`.
- **AND** no progress bar element is in the DOM.

#### Scenario: Progress bar appears only while running

- **GIVEN** the operator is on the Данные tab
- **AND** scheduler state is `running` with `tickers_total = 100`
  and `tickers_done = 30`
- **WHEN** the page renders
- **THEN** a `role="progressbar"` element appears with
  `aria-valuenow="30"`.

#### Scenario: Ticker row click opens drilldown

- **GIVEN** the operator is on the Данные tab
- **AND** the ticker table shows at least one row (e.g. SBER)
- **WHEN** the operator clicks the row
- **THEN** `useUiStore.openTicker('SBER')` is called
- **AND** `<TickerDrilldown>` opens with that symbol.

#### Scenario: Reset metadata requires explicit confirmation

- **GIVEN** the operator is on the Данные tab
- **WHEN** the operator clicks «Сбросить метаданные»
- **THEN** a `<ConfirmDialog danger …>` opens with title
  «Сбросить метаданные и перезагрузить всё?».
- **AND** confirming fires `useForceReset().mutateAsync()`.
- **AND** cancelling closes the dialog without firing the mutation.
