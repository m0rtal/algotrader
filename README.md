# Algotrader

Self-hosted algorithmic trading platform for MOEX (Moscow Exchange).

## Stack

- **Frontend**: React 19 + Vite 8 + TypeScript 5.7 (Wouter, TanStack Query, Zustand, Tailwind 4, shadcn-style components, Lightweight Charts, MSW)
- **Backend**: Python 3.11+ FastAPI + DuckDB + SQLite + OpenTelemetry (OTLP → Loki + Tempo via Grafana)
- **Data**: parquet on disk + DuckDB
- **ML**: XGBoost + hmmlearn (planned)
- **Observability**: structlog → OTLP, correlation IDs, secret scrubbing, health sampling
- **Spec**: OpenSpec (capability-driven, GIVEN/WHEN/THEN scenarios)

## Quick start

```bash
# Frontend (requires Node 22 + pnpm 9)
nvm use                 # or: nvm install 22
pnpm install
pnpm dev                # web at http://localhost:5173

# Backend (requires Python 3.11+)
cd apps/api
uv sync --extra dev
uv run uvicorn algotrader_api.main:app --host 127.0.0.1 --port 8000

# Observability stack (optional, for traces/logs)
cd ops/otel-collector
docker compose up -d    # Grafana at http://localhost:3000
```

MSW intercepts all `/api/*` calls in dev. Point the frontend at a real backend by setting `VITE_API_BASE_URL=http://127.0.0.1:8000/api` in `apps/web/.env.local`.

## Workspace layout

```
apps/
  web/                 # React + Vite frontend
  api/                 # FastAPI + DuckDB + SQLite backend
  api/src/algotrader_api/observability/  # tracing, logging, correlation, scrubbing
packages/
  shared/              # cross-cutting TypeScript types + Zod schemas
openspec/
  specs/               # canonical capability specs
  changes/             # active and archived changes
ops/
  otel-collector/      # docker-compose for OTel → Loki → Tempo → Grafana
data/                  # runtime data (bars parquet + sqlite state, gitignored)
```

## Scripts

```bash
pnpm dev              # start web dev server
pnpm build            # build all workspaces
pnpm test             # run all web tests
pnpm test:api         # run all backend tests (uv run pytest)
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

# trigger
