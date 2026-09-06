# Design: Frontend Shell

## Stack Decisions

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Runtime | Node 22 LTS | Current LTS, Vite 8 requires ≥20 |
| Package manager | pnpm 9 + workspaces | Disk-efficient, strict deps, monorepo native |
| Framework | React 19 | Industry standard, easier hire, OpenAlgo uses same |
| Build | Vite 8 | Fast HMR, Rolldown-based, native ESM |
| Language | TypeScript 5.7 (strict) | Catches API contract bugs at compile time |
| Router | Wouter 3 | 1.5KB, zero-config, single dashboard route, no need for React Router's data APIs |
| Server state | TanStack Query v5 | Industry standard, devtools, cache, refetch — non-negotiable for trading data |
| Client state | Zustand 5 | 1KB, no providers, no boilerplate; replaces Context + useReducer |
| Styling | Tailwind CSS 4 | Utility-first, theme via CSS variables, shadcn requires it |
| UI primitives | shadcn/ui (Radix-based) | Copy-paste, owned code, no version-lock; what OpenAlgo uses |
| Charts | TradingView Lightweight Charts 5 | 60 FPS, 50k+ candles, Apache 2.0; the only chart lib trading teams use |
| Mocking | MSW 2 | Intercepts fetch, swap to real API in one line later |
| Testing | Vitest 2 + React Testing Library | Vite-native, watch mode, ESM |
| Lint | ESLint 9 (flat config) | New flat config, faster, no legacy |
| Format | Prettier 3 | Single tool, no bike-shedding |
| Hooks | husky 9 + lint-staged | Pre-commit lint+format on staged files only |
| CI | GitHub Actions | Free, ubiquitous, well-supported by Vite/React |

## Repository Layout

```
algotrader/
├── apps/
│   ├── web/                        # React + Vite frontend
│   │   ├── src/
│   │   │   ├── app/                # bootstrap: App.tsx, main.tsx, providers, router
│   │   │   ├── pages/              # route targets (Dashboard.tsx only for v1)
│   │   │   ├── features/           # feature-based organization
│   │   │   │   ├── signals/        # SignalsTab + table + hook + types
│   │   │   │   ├── trades/
│   │   │   │   ├── portfolio/
│   │   │   │   ├── backtest/
│   │   │   │   ├── storage/        # bars (per-ticker)
│   │   │   │   ├── regime/         # HMM card
│   │   │   │   └── model/          # ML sidebar card
│   │   │   ├── components/         # shared layout
│   │   │   │   ├── layout/         # Topbar, KPIStr, Sidebar, LogStrip
│   │   │   │   ├── charts/         # EquityCurve, Sparkline, TickerDrilldown
│   │   │   │   └── ui/             # shadcn-generated (Button, Dialog, Tabs, etc)
│   │   │   ├── hooks/              # useTheme, useTicker
│   │   │   ├── lib/                # api, queryClient, format
│   │   │   ├── stores/             # zustand: uiStore
│   │   │   ├── types/              # api.ts (re-exports shared types)
│   │   │   ├── mocks/              # MSW: browser.ts, handlers.ts, data/*.json
│   │   │   ├── styles/             # globals.css (Tailwind imports + tokens)
│   │   │   └── test/               # setup.ts
│   │   ├── public/
│   │   ├── tests/                  # co-located *.test.tsx + e2e/ later
│   │   ├── index.html
│   │   ├── vite.config.ts
│   │   ├── tailwind.config.ts
│   │   ├── postcss.config.js
│   │   ├── tsconfig.json
│   │   ├── tsconfig.node.json
│   │   ├── eslint.config.js
│   │   ├── .prettierrc.json
│   │   └── package.json
│   └── api/                        # FastAPI (separate change, stub folder only)
├── packages/
│   └── shared/                     # cross-cutting types
│       ├── src/
│       │   ├── types/              # Signal, Trade, Portfolio, Bar, RegimeState, etc
│       │   └── index.ts
│       ├── tsconfig.json
│       └── package.json
├── .github/
│   └── workflows/
│       └── ci.yml
├── .husky/
│   ├── pre-commit
│   └── _/                          # husky internals
├── openspec/
│   ├── specs/
│   │   └── frontend-shell/
│   │       └── spec.md             # canonical capability spec
│   ├── changes/
│   │   ├── add-frontend-shell/
│   │   │   ├── proposal.md
│   │   │   ├── design.md           # this file
│   │   │   ├── tasks.md            # implementation breakdown
│   │   │   └── specs/
│   │   │       └── frontend-shell/
│   │   │           └── spec.md     # delta to apply
│   │   └── archive/
│   └── config.yaml
├── .gitignore
├── .nvmrc                          # node version
├── .prettierrc.json
├── .editorconfig
├── package.json                    # root, workspaces, scripts
├── pnpm-workspace.yaml
├── tsconfig.base.json              # shared TS config
├── README.md
└── AGENTS.md
```

## Data Flow

```
                       ┌──────────────────────┐
                       │ MSW (dev) / Real API │
                       │ (later)              │
                       └──────────┬───────────┘
                                  │ fetch /api/*
                                  ▼
                       ┌──────────────────────┐
                       │ api.ts (fetch wrapper│
                       │  + error handling)   │
                       └──────────┬───────────┘
                                  ▼
                       ┌──────────────────────┐
                       │ TanStack Query       │
                       │ (cache, refetch,     │
                       │  retries, devtools)  │
                       └──────────┬───────────┘
                                  ▼ useQuery
                       ┌──────────────────────┐
                       │ Feature hooks        │
                       │ (useSignals, etc)    │
                       └──────────┬───────────┘
                                  ▼
                       ┌──────────────────────┐
                       │ Feature components   │
                       │ (SignalsTable, etc)  │
                       └──────────────────────┘

Zustand (separate) ─────► uiStore (active tab, modal state, theme)
```

## Component Boundaries

- **App.tsx** — mounts Providers, sets up MSW in dev, renders Router
- **Providers.tsx** — QueryClient, TooltipProvider, ThemeProvider
- **Router.tsx** — single route `/` → Dashboard
- **Dashboard.tsx** — composition root: Topbar + KPIStr + 3-col grid + Tabs + LogStrip
- **features/<name>/<Name>Tab.tsx** — page-level component for that tab
- **features/<name>/use<Name>.ts** — TanStack Query hook, the only place that calls api
- **features/<name>/types.ts** — re-exports from `@algotrader/shared` (or local)
- **components/charts/** — pure chart wrappers around Lightweight Charts (no data fetching)

## Testing Strategy

- **Unit** — formatters, hooks (with `@testing-library/react-hooks`), pure utilities
- **Component** — `SignalsTable.test.tsx`: renders, sorts, filters, handles empty/loading
- **Integration** — `Dashboard.test.tsx`: full render with all providers + MSW handlers
- **MSW handlers** are shared between tests and dev — single source of truth for mock data shape

## Error Handling

- **Network errors** — TanStack Query retry 2x with exponential backoff; show inline error banner in component
- **Empty data** — every table/chart has an empty state with a "Что-то не так" message
- **Type errors** — Zod schemas in `@algotrader/shared` parse API responses at runtime; bad data → console.error + fallback to empty
- **MSW disabled in prod** — `import.meta.env.DEV` gates the worker

## Performance

- TanStack Query caches for 30s; refetch on window focus
- Charts lazy-loaded via `React.lazy` so initial bundle stays small
- Tables use native virtualization only if >100 rows (we have <50 for v1)
- Bundle budget: <300KB gzipped for initial route

## Migration Path

The current `dashboard.html` is the design source of truth. The change proceeds:
1. Scaffold monorepo + apps/web with Vite
2. Set up Tailwind 4 + shadcn init
3. Port dashboard.html → `Dashboard.tsx` + components, one tab at a time
4. Replace hand-rolled SVG with Lightweight Charts for equity curve
5. Add MSW with mock data extracted from current HTML
6. Add tests as we go (target: 1 test per feature hook + per table component)
7. CI green; archive this change; move to next sub-project

## Risks

- **pnpm not in PATH** — install via `npm i -g --prefix ~/.local pnpm` (same pattern as we did for openspec)
- **shadcn init** requires interactive prompts — use `npx shadcn@latest init -y` with flags
- **MSW v2 API change** — Service Worker setup differs from v1; pin to 2.6+
- **Tailwind 4** is new (Jan 2025) — some shadcn components assume v3; verify on init
- **React 19** strict mode double-render can break Lightweight Charts if not handled (mount/unmount in useEffect cleanup)
