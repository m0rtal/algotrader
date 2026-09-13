# Design — merge-data-tab

## Stack

Same as the rest of the frontend: React 19 + Vite 6 + Tailwind 4,
zustand UI store, React Query for server state. No new dependencies.

## Layout

```
Dashboard
├── Topbar (unchanged)
├── KPIStr (unchanged)
├── Tab strip [Сигналы · Сделки · Портфель · Бэктест · Данные]   (was 6, now 5)
└── Main panel, single column, p-6 gap-6

  ┌── DataTab ────────────────────────────────────────────────┐
  │                                                            │
  │  Section 1 · System state (read)                          │
  │  ┌─ Card · Состояние  · Scheduler: idle / running / …     │
  │  ┌─ Card · Баров на диске · total_bars (useBackfillStatus)│
  │  ┌─ Card · Период · min(firstDate) → max(lastDate)         │
  │  ┌─ Card · Полнота · (period_days − gaps) / period_days    │
  │                                                            │
  │  Section 2 · Очередь бэкфилла (write)                     │
  │  ┌─ Pending counters · N новых / M устаревших / K ошибок  │
  │  ┌─ Progress bar · shown only while running, role=         │
  │  │       progressbar with aria-valuenow                    │
  │  ┌─ Buttons · Запустить бэкфилл / Остановить / Сбросить    │
  │       метаданные (opens ConfirmDialog when danger)         │
  │                                                            │
  │  Section 3 · Тикеры (inventory)                            │
  │  ┌─ Table header · Тикер · Бары · Размер · Период · Гэпы │
  │  ┌─ Rows · sort by bars desc, max-h-360, scroll-y         │
  │       Click row → useUiStore.openTicker(symbol)            │
  │                                                            │
  └────────────────────────────────────────────────────────────┘
```

## Data Flow

The DataTab subscribes to three hooks in parallel:

```
useBackfillStatus()         ─ 5 s polling
usePendingCount(60_000)     ─ 60 s polling
useTickers()                ─ default React Query
```

Each hook lives in `apps/web/src/lib/hooks.ts`. They are reused
without modification — StorageTab's `data.reduce((s, t) => s + t.bars, 0)`
becomes `status.total_bars`, which is the same value computed
server-side and stays single-source-of-truth.

`useUiStore.openTicker(symbol)` is called from row clicks. The
existing `<TickerDrilldown>` is mounted at the Dashboard root and
opens via the same store, so the merged page reuses the existing
drilldown flow without changes.

Mutations: `useStartBackfill`, `useStopBackfill`, `useForceReset`
each `invalidateQueries(['backfill-status'])` and
`invalidateQueries(['backfill-pending'])` on success — preserved
unchanged.

## Component Composition

```
DataTab
├── <header className="flex items-center justify-between">
│   ├── <h2>Данные</h2>
│   └── <span>Ежедневно в 02:00 МСК</span>
├── <section data-testid="data-status">  ← system state cards grid
├── <section data-testid="data-queue">    ← queue counters + buttons
├── <section data-testid="data-tickers">  ← table
└── <ConfirmDialog danger open={showResetConfirm && pending.data}
       title="Сбросить метаданные и перезагрузить всё?"
       body={…}
       confirmLabel="Сбросить метаданные"
       cancelLabel="Отмена"
       … />
```

The ConfirmDialog for reset is reused verbatim from
`BackfillTab.tsx`. The BackfillEvent SSE hook (`useBackfillEvents`)
was removed in a prior commit (`57fb1ec`) and is not reintroduced —
text events live in the global `LogStrip`.

## Accessibility

- Each section is `<section>` with a stable `data-testid` for test
  selectors and a heading hierarchy `h2` (page) → `h3` (section).
- Progress bar retains `role="progressbar"` + `aria-valuenow` and
  the wrapper keeps `aria-live="polite"`.
- Status pill (`Sandbox` / `live` / `idle` / `running`) uses
  the existing `<FieldStatus>` style token; decorative
  `aria-hidden="true"` is preserved.
- Table rows are `<button>` per row (not anchor tags) so keyboard
  users can activate with Enter / Space; the existing focus-visible
  ring from globals.css covers them.

## Tests

`DataTab.test.tsx` consolidates the previous BackfillTab + StorageTab
suites. Key assertions:

- Renders status section with `total_bars` from useBackfillStatus
  (not from `data.reduce` — single source of truth).
- Renders pending section with 4 counters + 3 buttons when status is
  idle.
- Progress bar element appears only when `tickers_total > 0`.
- Saving-tokens fan-out still works (one POST → one PUT when
  tokenDraft is non-empty). **This is unrelated to the merge**;
  covered in the broker-section-redesign change.
- Clicking a ticker row calls `openTicker(symbol)`.

## Risks

- **Single source of truth for total_bars.** Status may report
  `total_bars` that diverges from `sum(data.bars)` if the two
  endpoints are served from different snapshots. Mitigation:
  the status endpoint is documented as the source of truth; the
  table does not re-aggregate.
- **Test surface migration.** Reuses `defaultPending`,
  `defaultStatus`, `sampleTickers` fixtures verbatim. The 23
  existing tests (18 BackfillTab + 5 StorageTab) are replaced
  by a smaller DataTab suite that asserts the integrated flow.
  Net code/test reduction is the goal of the merge, not a
  coverage loss.
