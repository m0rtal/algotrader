# Proposal: Settings

## Why
The dashboard currently has no place for user-controlled configuration:
- Tinkoff broker token (when backend ships)
- Risk limits (max drawdown %, max position size, kill-switch threshold)
- ML pipeline knobs (model version, retrain interval, threshold)
- Data source selection (Tinkoff vs MOEX ISS, cache TTL)
- Logging preferences (log level, retention)

Right now these would live in `.env` files or hardcoded constants — neither is operator-friendly. As the project moves toward live trading, **misconfigured risk limits are the #1 way to lose money**. The settings page is the operator's safety surface.

## What Changes
Add a dedicated `/settings` route accessible from a gear icon in the Topbar. The page is organized into 4 sections (Broker, Risk, ML, Data), each with typed form fields and inline validation. Settings are persisted server-side; the UI reads/writes via REST endpoints (`GET /api/settings`, `PUT /api/settings`).

For v1, the backend is mocked via MSW (consistent with the rest of the frontend). The contract is stable: when `add-backend-api` ships, only the MSW handlers change — the frontend, types, and tests stay identical.

The token field is masked (`••••••ABCD`) and never echoed in full. The API request payload uses `redacted: true` for the PUT — the backend stores the existing value, not the masked display. The Settings page also surfaces **danger zone** with explicit confirmations for live-trading arming and kill-switch reset.

## Impact
- **New spec capability:** `settings`
- **New files:**
  - `apps/web/src/features/settings/` (SettingsTab with sub-tabs: Broker/Risk/ML/Data)
  - `apps/web/src/pages/Settings.tsx` (route target)
  - `apps/web/src/lib/settings.ts` (Zod schemas, fetch wrappers)
  - `apps/web/src/stores/settingsStore.ts` (Zustand for current values + dirty tracking)
  - `apps/web/src/mocks/handlers.ts` — new `/api/settings` GET/PUT handlers
  - `packages/shared/src/index.ts` — Settings types
- **Modified files:**
  - `apps/web/src/app/router.tsx` — add `/settings` route
  - `apps/web/src/pages/Dashboard.tsx` — add gear icon link in Topbar (or top-level)
  - `openspec/specs/frontend-shell/spec.md` — superseded by `settings`

## Non-Goals
- **Auth / multi-user:** Single-user sandbox; auth is a separate change.
- **Encrypted at rest:** Backend stores token in plaintext in v1. Production deployment must use a secrets manager — that is a deployment concern, not a frontend concern.
- **Audit log of setting changes:** Out of scope; the broker integration will log trade events but not config events.
- **Import/export of settings as JSON:** Nice to have, deferred.
- **Risk limits enforcement:** The page stores the limits; the actual enforcement happens in the broker/strategy layer, which doesn't exist yet.
