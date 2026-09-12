# Design — ui-shell

## Stack

- React 19 + Vite 6 + TypeScript 5.7
- Tailwind CSS 4 (via `@tailwindcss/vite`), tokens in `@theme {}` block
- MSW (Mock Service Worker) for dev `/api/*` fallback; real backend
  via `VITE_API_BASE_URL`
- `@tanstack/react-query` for data fetching and caching
- Vitest + Testing Library + jsdom for unit tests

## Layout

```
apps/web/src/
├── styles/globals.css          # @theme tokens + base layer (focus ring, reduced-motion)
├── app/                        # App.tsx, router, providers (QueryClient), main
├── components/
│   ├── layout/                 # Topbar, KPIStr, Sidebar, RightRail, LogStrip
│   └── charts/                 # TickerDrilldown (modal), EquityCurve, Sparkline
├── features/
│   ├── backfill/BackfillTab.tsx
│   ├── backtest/   portfolio/  signals/   storage/   trades/
│   └── settings/   (sections, ConfirmDialog, Toast, Field)
├── lib/   (api.ts, hooks.ts, format.ts, queryClient.ts)
├── stores/ (uiStore, settingsStore — zustand)
├── mocks/  (handlers, server, passthrough)
└── test/setup.ts (MSW + ResizeObserver mock)
```

Dashboard renders the shell:

```
┌─ Topbar ────────────────────────────────────────┐
│ ALGOTRADER · MOEX  | IMOEX 3142.8 +0.42% | ⚙    │
├─ KPIStr ────────────────────────────────────────┤
│ [6 KPI tiles]                                   │
├─ Sidebar(240) ─┬─ Tab strip + main ──┬─ RightRail(320) ─┤
│ Regime /       │ Signals/Trades/     │ ML Model,       │
│ Universe       │ Portfolio/Backtest/ │ Top features,   │
│                │ Storage/Backfill    │ Pipeline status │
├─ LogStrip (fixed bottom, 4 last events) ───────┤
└────────────────────────────────────────────────┘
```

## Data Flow

- `lib/api.ts` reads `VITE_API_BASE_URL` (or `/api` when unset).
- MSW intercepts `/api/*` in dev when the env var is unset (passthrough
  to the real backend in production builds).
- React Query owns all server state; components subscribe via
  `useKpis()`, `useSignals()`, `useRegime()`, `useBackfillStatus()`,
  etc. — all built on `makeHook()` in `lib/hooks.ts`.
- UI-only state (selected tab, open ticker) lives in `stores/uiStore`.
- Settings state lives in `stores/settingsStore` with optimistic
  version-stamping for 409 conflict resolution.

### Accessibility flow

- `:focus-visible` outline declared once in `globals.css` covers every
  interactive element by selector.
- Modal components (`TickerDrilldown`, `ConfirmDialog`) capture focus
  on mount and restore it on unmount via `useEffect` + `useRef`.
- `Toast` uses `role="status" aria-live="polite"` so screen readers
  announce save/error events.
- `prefers-reduced-motion` media query disables `.animate-pulse`.

### Token flow

```
@theme {}                    Tailwind utilities
  --color-bg ──────────────►  bg-bg, text-bg
  --color-surface ─────────►  bg-surface
  --color-accent ──────────►  text-accent, border-accent
  ...
  --background (alias) ───►  bg-[var(--background)]   (legacy)
  --card (alias) ─────────►  bg-[var(--card)]        (legacy)
```

Both naming layers coexist by design until all callers migrate from
`bg-[var(--card)]` to `bg-surface`; new components SHOULD prefer the
`bg-*` / `text-*` / `border-*` utilities.

## Risks

1. **Coverage gate** (≥95% on 4 metrics). New code paths in
   `ConfirmDialog` and `TickerDrilldown` (focus capture/restore) need
   test coverage; covered by existing tests for the visible-button paths,
   but the new `useEffect` lifecycle branches need explicit tests before
   merging.
2. **Pre-existing test**: `App.test.tsx` hangs in jsdom under the full
   suite. Reproduces on `HEAD` without any of this change's edits; not
   a regression introduced here. Flagged in `tasks.md` §8 as separate.
3. **MSW parity**: any new `/api/*` route added to the backend must
   gain an MSW handler in `apps/web/src/mocks/` to keep dev without
   the backend running.
4. **Theme drift**: spec covers light/dark only via `--color-*` swap.
   Roadmap item `theming-light` will add a theme switcher; the
   `unprefixed` aliases (`--background`, `--card`, etc.) will need to
   follow the active theme, not `--color-bg` directly. The current
   alias layer is one indirection that makes this future swap a 1-line
   change.
