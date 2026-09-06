# frontend-shell Specification (delta)

## ADDED Requirements

### Requirement: Project Layout

The system SHALL organize the monorepo with pnpm workspaces containing:
- `apps/web` — React 19 + Vite 8 + TypeScript frontend
- `apps/api` — placeholder for future FastAPI backend
- `packages/shared` — cross-cutting TypeScript types

#### Scenario: New developer clones and runs

- GIVEN a developer has cloned the repository
- AND has Node 22 and pnpm 9 installed
- WHEN they run `pnpm install`
- THEN all workspace dependencies resolve without errors
- AND `pnpm dev` starts the web dev server on port 5173

### Requirement: Web Application Stack

The system SHALL use the following frontend stack:
- React 19 with strict mode enabled
- Vite 8 as the build tool with HMR
- TypeScript 5.7 in strict mode
- Wouter 3 for client-side routing
- TanStack Query v5 for server state management
- Zustand 5 for client UI state
- Tailwind CSS 4 with CSS-variable design tokens
- shadcn/ui (Radix-based) for component primitives
- TradingView Lightweight Charts 5 for financial visualizations
- MSW 2 for API mocking in development
- Vitest 2 + React Testing Library for testing

#### Scenario: Stack is consistent across the codebase

- GIVEN a contributor adds a new feature module
- WHEN they implement a chart, list, or data table
- THEN they SHALL use Lightweight Charts, shadcn Table, and TanStack Query respectively
- AND SHALL NOT introduce alternative libraries (no Recharts, no React Router, no SWR)

### Requirement: Single Dashboard Route

The system SHALL expose a single client-side route `/` rendering the Dashboard page.

#### Scenario: User navigates to root

- GIVEN a user opens the application
- WHEN the URL is `/` or any other path
- THEN the Dashboard page renders
- AND no additional routes are required for v1

### Requirement: Tab Navigation

The system SHALL provide 5 in-page tabs inside the Dashboard: Signals, Trades, Portfolio, Backtest, Storage.

#### Scenario: User switches tabs

- GIVEN the Dashboard is rendered with active tab "Signals"
- WHEN the user clicks the "Trades" tab
- THEN the active tab becomes "Trades"
- AND the TradesTab content replaces the SignalsTab content
- AND the URL does NOT change (tabs are not routes)

#### Scenario: Active tab persists in state

- GIVEN the user has selected the "Backtest" tab
- WHEN any component re-renders for an unrelated reason
- THEN the "Backtest" tab remains active
- AND the selection survives page refresh via Zustand persistence

### Requirement: API Mocking

The system SHALL intercept all `/api/*` requests via MSW when running in development mode, serving responses from local JSON fixtures.

#### Scenario: Developer runs `pnpm dev` with no backend

- GIVEN no FastAPI backend is running
- WHEN the web app starts in development mode
- THEN MSW registers a service worker before the first request
- AND all `/api/*` calls return JSON from `src/mocks/data/`
- AND the Network tab shows the requests as `(from service worker)`

#### Scenario: MSW is disabled in production

- GIVEN the web app is built with `pnpm build`
- WHEN it runs in production mode (`import.meta.env.PROD === true`)
- THEN MSW does NOT register a service worker
- AND `/api/*` calls go directly to the real backend URL

### Requirement: Type Safety

The system SHALL share TypeScript types between features via the `@algotrader/shared` workspace package, and SHALL parse all API responses with Zod schemas at runtime.

#### Scenario: API response shape changes

- GIVEN the backend changes the shape of `/api/signals` response
- WHEN the frontend receives the new response
- THEN Zod parsing throws a runtime error
- AND the component renders an empty state with a console.error
- AND a TypeScript compile error appears for the type mismatch

### Requirement: Testing

The system SHALL include unit and component tests written with Vitest and React Testing Library, runnable via `pnpm test`.

#### Scenario: Tests run on CI

- GIVEN a developer pushes to a branch
- WHEN GitHub Actions runs the CI workflow
- THEN `pnpm test` executes all unit and component tests
- AND the workflow fails if any test fails or coverage drops below thresholds

#### Scenario: New feature has a test

- GIVEN a developer adds a new feature module under `features/<name>/`
- WHEN they open a pull request
- THEN the module SHALL include at least one test file (`*.test.ts` or `*.test.tsx`)

### Requirement: Code Quality

The system SHALL enforce code style and quality via ESLint 9 (flat config), Prettier 3, husky pre-commit hooks, and lint-staged.

#### Scenario: Developer commits code with lint errors

- GIVEN a developer stages a file with ESLint errors
- WHEN they run `git commit`
- THEN the pre-commit hook runs lint-staged
- AND ESLint auto-fixes what it can
- AND remaining errors block the commit with a clear message

#### Scenario: Developer formats code

- GIVEN a developer stages any file
- WHEN the pre-commit hook runs
- THEN Prettier formats the staged files
- AND the commit proceeds with formatted code

### Requirement: CI Pipeline

The system SHALL run continuous integration on every push and pull request via GitHub Actions, executing lint, typecheck, test, and build in parallel where possible.

#### Scenario: PR is opened

- GIVEN a developer opens a pull request
- WHEN the CI workflow triggers
- THEN it runs `pnpm install`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build`
- AND reports status back to the PR

### Requirement: Dark Theme

The system SHALL render exclusively in dark theme.

#### Scenario: User toggles system theme

- GIVEN the application uses dark theme only
- WHEN the user's OS is in light mode
- THEN the application still renders in dark theme
- AND no theme toggle is exposed in the UI

### Requirement: Desktop-First Layout

The system SHALL optimize the layout for desktop screens (≥1280px wide) and provide basic responsive behavior down to 768px.

#### Scenario: User opens on 1920x1080

- GIVEN the viewport is 1920x1080
- WHEN the Dashboard renders
- THEN the 3-column grid (sidebar / main / right rail) is visible
- AND no horizontal scroll occurs

#### Scenario: User opens on 768px tablet

- GIVEN the viewport is 768px wide
- WHEN the Dashboard renders
- THEN the right rail collapses below the main content
- AND the sidebar remains in a single column

## MODIFIED Requirements

(none — this is a greenfield change)

## REMOVED Requirements

(none)

## Non-Goals (deferred to other changes)

- Backend integration (capability: `backend-api`)
- Authentication and authorization (capability: `auth`)
- Real-time WebSocket streaming (capability: `data-layer`)
- Light theme (capability: `theming-light`)
- Internationalization (capability: `i18n`)
- Mobile-first responsive design (capability: `mobile-responsive`)
- Production deployment configuration (capability: `deployment`)
