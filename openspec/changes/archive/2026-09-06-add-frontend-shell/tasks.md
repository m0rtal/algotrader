# Tasks: add-frontend-shell

## 1. Monorepo scaffold
- [ ] 1.1. Init git, .gitignore (Node, Vite, IDE), .nvmrc (22), .editorconfig
- [ ] 1.2. Create root `package.json` with workspace scripts (`dev`, `build`, `lint`, `test`, `format`, `typecheck`)
- [ ] 1.3. Create `pnpm-workspace.yaml` with `apps/*` and `packages/*`
- [ ] 1.4. Install pnpm globally if not present
- [ ] 1.5. Add root devDependencies: prettier 3, eslint 9 (shared config later)
- [ ] 1.6. Create `tsconfig.base.json` with strict settings

## 2. apps/web scaffold
- [ ] 2.1. `pnpm create vite@latest apps/web -- --template react-ts` (or manual scaffold)
- [ ] 2.2. Verify dev server runs (`pnpm dev`)
- [ ] 2.3. Add deps: react@19, react-dom@19, wouter, @tanstack/react-query, @tanstack/react-query-devtools, zustand
- [ ] 2.4. Add devDeps: @types/react, @types/react-dom, @vitejs/plugin-react, vite@8
- [ ] 2.5. Configure `tsconfig.json` extending base, add path aliases (`@/*`, `@features/*`, `@components/*`, `@lib/*`)
- [ ] 2.6. Update `vite.config.ts` with `@/*` alias, MSW service worker copy

## 3. Tailwind 4 + shadcn
- [ ] 3.1. Install tailwindcss@4, @tailwindcss/vite, postcss, autoprefixer
- [ ] 3.2. Configure `vite.config.ts` with tailwind plugin
- [ ] 3.3. Create `src/styles/globals.css` with `@import "tailwindcss"` + design tokens (CSS variables matching current dashboard palette)
- [ ] 3.4. Run `npx shadcn@latest init -y` with proper config (style: new-york, base color: zinc)
- [ ] 3.5. Add needed shadcn components: button, card, tabs, dialog, badge, table, scroll-area, separator, tooltip, dropdown-menu
- [ ] 3.6. Verify Tailwind classes render in dev

## 4. Lightweight Charts integration
- [ ] 4.1. Install `lightweight-charts@5`
- [ ] 4.2. Create `src/components/charts/EquityCurve.tsx` (wrapper component with cleanup)
- [ ] 4.3. Create `src/components/charts/Sparkline.tsx`
- [ ] 4.4. Create `src/components/charts/TickerChart.tsx` (drill-down modal chart)
- [ ] 4.5. Test: chart renders, updates on data change, unmounts cleanly

## 5. App shell + routing
- [ ] 5.1. Create `src/app/main.tsx` (entry, mounts `<App/>`)
- [ ] 5.2. Create `src/app/providers.tsx` (QueryClient, Tooltip, Theme)
- [ ] 5.3. Create `src/app/router.tsx` (Wouter: `/` → Dashboard)
- [ ] 5.4. Create `src/app/App.tsx` (composition: Providers + Router)
- [ ] 5.5. Add `@/styles/globals.css` import in main.tsx
- [ ] 5.6. Verify Dashboard renders

## 6. Layout components (port from dashboard.html)
- [ ] 6.1. Create `src/components/layout/Topbar.tsx` (brand + meta)
- [ ] 6.2. Create `src/components/layout/KPIStr.tsx` (6 KPI cells)
- [ ] 6.3. Create `src/components/layout/Sidebar.tsx` (Regime card + Universe list)
- [ ] 6.4. Create `src/components/layout/LogStrip.tsx` (footer logs)
- [ ] 6.5. Create `src/components/layout/Grid.tsx` (3-col CSS grid layout)

## 7. packages/shared types
- [ ] 7.1. Create `packages/shared/package.json` with name `@algotrader/shared`
- [ ] 7.2. Create `packages/shared/tsconfig.json`
- [ ] 7.3. Define types: `Signal`, `Trade`, `Portfolio`, `Position`, `Bar`, `Ticker`, `RegimeState`, `MLModel`, `WalkForwardFold`
- [ ] 7.4. Export from `packages/shared/src/index.ts`
- [ ] 7.5. Add to apps/web dependencies: `"@algotrader/shared": "workspace:*"`

## 8. MSW mocks
- [ ] 8.1. Install `msw@2` in apps/web devDeps
- [ ] 8.2. Run `npx msw init public/ --save` to copy service worker
- [ ] 8.3. Create `src/mocks/data/signals.json` (12 signals from current HTML)
- [ ] 8.4. Create `src/mocks/data/trades.json` (12 trades)
- [ ] 8.5. Create `src/mocks/data/portfolio.json` (12 positions)
- [ ] 8.6. Create `src/mocks/data/bars/*.json` (one per ticker)
- [ ] 8.7. Create `src/mocks/handlers.ts` with REST endpoints `/api/signals`, `/api/trades`, `/api/portfolio`, `/api/bars/:ticker`, etc.
- [ ] 8.8. Create `src/mocks/browser.ts` (setupWorker)
- [ ] 8.9. Mount MSW only in dev: `if (import.meta.env.DEV) { await worker.start() }` in main.tsx
- [ ] 8.10. Verify Network tab shows `200` from intercepted requests

## 9. API client + TanStack Query
- [ ] 9.1. Create `src/lib/queryClient.ts` (default options: staleTime 30s, retry 2)
- [ ] 9.2. Create `src/lib/api.ts` (fetch wrapper: base URL, JSON parse, error throwing)
- [ ] 9.3. Create `src/lib/format.ts` (formatRUB, formatPct, formatDate, formatTime)
- [ ] 9.4. Create feature hooks: `useSignals()`, `useTrades()`, `usePortfolio()`, `useBars(ticker)`, `useRegime()`, `useModel()`

## 10. Feature tabs
- [ ] 10.1. `features/signals/SignalsTab.tsx` + `SignalsTable.tsx`
- [ ] 10.2. `features/trades/TradesTab.tsx` + `TradesTable.tsx`
- [ ] 10.3. `features/portfolio/PortfolioTab.tsx` + `PositionsTable.tsx`
- [ ] 10.4. `features/backtest/BacktestTab.tsx` + `FoldsTable.tsx`
- [ ] 10.5. `features/storage/StorageTab.tsx` + `BarsTable.tsx` (with sparklines)
- [ ] 10.6. `features/regime/RegimeCard.tsx` (in sidebar)
- [ ] 10.7. `features/model/ModelCard.tsx` (in sidebar) + `FeaturesList.tsx`

## 11. Zustand store
- [ ] 11.1. Create `src/stores/uiStore.ts` with state: `activeTab`, `selectedTicker`, `theme`
- [ ] 11.2. Create `useTickerDrilldown()` hook that opens modal on row click

## 12. Ticker drill-down modal
- [ ] 12.1. Create `src/components/charts/TickerDrilldown.tsx` (Dialog from shadcn)
- [ ] 12.2. Wire to uiStore: clicking a ticker row sets `selectedTicker`
- [ ] 12.3. Fetches `/api/bars/:ticker` via TanStack Query
- [ ] 12.4. Renders stat grid + 30-day chart
- [ ] 12.5. Closes on Escape, click outside, X button

## 13. Testing setup
- [ ] 13.1. Install `vitest@2`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `jsdom`
- [ ] 13.2. Create `src/test/setup.ts` (jest-dom matchers, MSW server start)
- [ ] 13.3. Create `vitest.config.ts` (jsdom env, alias to vite.config)
- [ ] 13.4. Add `test` script to apps/web/package.json
- [ ] 13.5. Write `src/lib/format.test.ts` (5 cases)
- [ ] 13.6. Write `src/lib/api.test.ts` (success + error)
- [ ] 13.7. Write `src/components/charts/EquityCurve.test.tsx` (renders without crash)
- [ ] 13.8. Write `src/features/signals/SignalsTable.test.tsx` (renders, empty, loading states)

## 14. Lint + format
- [ ] 14.1. Install `eslint@9`, `@typescript-eslint`, `eslint-plugin-react`, `eslint-plugin-react-hooks`, `eslint-plugin-jsx-a11y`
- [ ] 14.2. Create `eslint.config.js` (flat config, extends recommended)
- [ ] 14.3. Create `.prettierrc.json` (singleQuote, semi, 100 width)
- [ ] 14.4. Install `husky@9`, `lint-staged`
- [ ] 14.5. Setup husky: `pnpm exec husky init`
- [ ] 14.6. Add `.husky/pre-commit`: `pnpm exec lint-staged`
- [ ] 14.7. Add lint-staged config to root package.json
- [ ] 14.8. Verify pre-commit hook runs on a test change

## 15. CI
- [ ] 15.1. Create `.github/workflows/ci.yml` (trigger: push, pull_request)
- [ ] 15.2. Steps: checkout, setup-node 22, install pnpm, cache pnpm store, `pnpm install --frozen-lockfile`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm build`
- [ ] 15.3. Verify workflow runs on a dummy PR

## 16. Documentation
- [ ] 16.1. Write root `README.md` (project intro, stack, dev commands)
- [ ] 16.2. Write `apps/web/README.md` (web-specific notes)
- [ ] 16.3. Write root `AGENTS.md` (agent guidance, this project)
- [ ] 16.4. Run `openspec validate add-frontend-shell --strict`

## 17. Archive
- [ ] 17.1. Run `openspec archive add-frontend-shell --yes` (move change to `changes/archive/`)
- [ ] 17.2. Verify `openspec/specs/frontend-shell/spec.md` exists as the canonical capability spec

## Out of scope (separate changes)
- `add-data-layer` — WebSocket real-time streams, optimistic updates
- `add-charts-integration` — indicators (RSI, MACD), multi-timeframe
- `add-backend-api` — FastAPI + tinkoff integration
- `add-auth` — JWT, protected routes
- `add-i18n` — react-i18next
