# Algotrader

Self-hosted algorithmic trading platform for MOEX (Moscow Exchange).

## Stack

- **Frontend**: React 19 + Vite 8 + TypeScript 5.7 (Wouter, TanStack Query, Zustand, Tailwind 4, shadcn-style components, Lightweight Charts, MSW)
- **Backend**: FastAPI + tinkoff-python (planned)
- **Data**: parquet on disk + DuckDB
- **ML**: XGBoost + hmmlearn (planned)
- **Spec**: OpenSpec (capability-driven, GIVEN/WHEN/THEN scenarios)

## Quick start

```bash
# Requires Node 22 + pnpm 9
nvm use                 # or: nvm install 22
pnpm install
pnpm dev                # web at http://localhost:5173
```

MSW intercepts all `/api/*` calls in dev — no backend needed.

## Workspace layout

```
apps/
  web/                 # React + Vite frontend
  api/                 # FastAPI backend (placeholder)
packages/
  shared/              # cross-cutting TypeScript types + Zod schemas
openspec/
  specs/               # canonical capability specs
  changes/             # active and archived changes
```

## Scripts

```bash
pnpm dev              # start web dev server
pnpm build            # build all workspaces
pnpm test             # run all tests
pnpm lint             # eslint
pnpm typecheck        # tsc --noEmit
pnpm format           # prettier
```

## OpenSpec workflow

```bash
openspec list                 # active changes
openspec status <change>      # task progress
openspec validate <change>   # spec validation
openspec archive <change>     # archive after implementation
```

See `openspec/specs/frontend-shell/spec.md` for the v1 frontend spec.

## Roadmap (capabilities)

- ✅ `frontend-shell` — React shell with MSW mocks
- ⏳ `data-layer` — WebSocket real-time, optimistic updates
- ⏳ `charts` — indicators (RSI, MACD), multi-timeframe
- ⏳ `backend-api` — FastAPI + tinkoff
- ⏳ `auth` — JWT, protected routes
- ⏳ `theming-light` — light theme variant

## License

Private / TBD

# baseline test
