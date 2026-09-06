# Proposal: Frontend Shell

## Why

Trading dashboard for MOEX algorithmic trading. The project needs a frontend foundation that:
- Renders real-time data from a future FastAPI backend
- Hosts interactive charts and dense data tables
- Lets one developer (you) iterate fast without lock-in
- Survives growth from 1 user → small team without rewrite

The current state is a single `dashboard.html` file (56KB) with mock data. Moving to a typed, componentized, testable frontend unlocks the next sub-projects (data layer, charts, then backend).

## What Changes

Establish `apps/web` (React 19 + Vite 8 + TypeScript) inside a pnpm monorepo, with:
- Wouter for client-side routing (single dashboard route, 5 in-page tabs)
- TanStack Query for server state
- Zustand for client UI state
- Tailwind 4 + shadcn/ui for components
- Lightweight Charts 5 for financial visualizations
- MSW (Mock Service Worker) for offline dev until backend exists
- Vitest + React Testing Library for tests
- ESLint + Prettier + husky + lint-staged for code quality
- GitHub Actions CI on every PR

The backend (FastAPI) is **not** in this change. It is a separate sub-project (capability `backend-api`).

## Impact

- Affected specs: NEW `frontend-shell` capability
- Affected code: NEW files under `apps/web/`, `packages/shared/`, root config
- Existing files: NONE (greenfield)
- Timeline: ~2-3 days of focused work to ship a working `npm run dev` with one full tab (Signals) and the rest as empty tabs

## Non-Goals (deferred)

- Backend integration (separate change)
- Authentication
- Real-time WebSocket streams (polling via TanStack Query is enough for v1)
- Light theme (dark only)
- i18n (Russian UI only)
- Mobile-first responsive (desktop-first; basic responsive only)
- Production deployment
