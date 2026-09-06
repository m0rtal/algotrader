# Design: add-settings

## Stack
- New feature folder `apps/web/src/features/settings/` with sub-components per section
- New page `apps/web/src/pages/Settings.tsx` registered in router at `/settings`
- New store `apps/web/src/stores/settingsStore.ts` (Zustand) for current values, dirty state, save status
- Shared types in `packages/shared/src/index.ts` (Zod schemas for Settings, SectionKey)
- MSW handlers in `apps/web/src/mocks/handlers.ts` for `GET/PUT/DELETE /api/settings`
- Test coverage target: ≥95% (Vitest thresholds already enforce this)

## Layout

```
apps/web/src/
  features/settings/
    SettingsTab.tsx           # container with 4 sub-tabs
    sections/
      BrokerSection.tsx       # environment, token, accountId
      RiskSection.tsx         # drawdown, position size, kill switch
      MLSection.tsx           # model version, retrain, confidence
      DataSection.tsx         # source, cache, history
    DangerZone.tsx            # reset all + arm live
    ConfirmDialog.tsx         # shared confirm modal
    settingsStore.ts          # local Zustand (separate from global uiStore)
  pages/
    Settings.tsx              # route target: header + SettingsTab
  components/
    layout/SettingsLink.tsx   # gear icon in Topbar
  mocks/
    handlers.ts               # new /api/settings endpoints
    data.ts                   # settings mock
  stores/
    settingsStore.ts
packages/shared/src/
  index.ts                    # + SettingsSchema, SettingsKeySchema
```

## Data Flow

```
SettingsTab mounts
  ↓
useSettings() hook (TanStack Query)
  ↓
GET /api/settings
  ↓
SettingsSchema.parse(json)  ← runtime validation
  ↓
settingsStore.setValues(parsed)
  ↓
form renders with values (or defaults on 404)
  ↓
operator edits field
  ↓
settingsStore.markDirty(field)
  ↓
operator clicks Save
  ↓
PUT /api/settings { ...values, version: current }
  ↓
backend returns { ...updated, version: "new-hash" }
  ↓
settingsStore.setValues(updated)
toast.success("Сохранено")
```

## Component Boundaries

- `SettingsTab` — owns 4 sub-tabs, manages active section, mounts all 4 sections
- `BrokerSection` (and other sections) — controlled component, props: `values`, `onChange`, `disabled`
- `DangerZone` — separate from sections, full-width, red border
- `ConfirmDialog` — reusable for any destructive action
- `settingsStore` (Zustand) — local state for current values + dirty + saving state
  - **Why not in `uiStore`?** Settings are page-local + form state. uiStore is for cross-page UI (active tab, modal). Separation prevents re-renders.

## State Management

```ts
// settingsStore.ts
interface SettingsState {
  values: Settings;             // current displayed values
  baseline: Settings | null;    // last-saved values (for dirty check)
  saving: boolean;
  loading: boolean;
  error: string | null;

  setValues: (v: Settings) => void;
  update: <K extends keyof Settings>(section: K, field: string, value: unknown) => void;
  isDirty: () => boolean;
  save: () => Promise<void>;
  reset: () => void;            // back to baseline
}
```

## API Contract

```http
GET /api/settings
→ 200 { values: Settings, version: string, updatedAt: string }
→ 404 { error: "no settings" }   # first run, use defaults

PUT /api/settings
Body: { values: Settings, version: string }
→ 200 { values: Settings, version: string, updatedAt: string }
→ 409 { error: "version conflict", current: Settings }  # concurrent edit
→ 400 { error: "validation", issues: ZodIssue[] }       # bad input

DELETE /api/settings
→ 204
```

## Token Handling

- **Display:** always masked as `••••••ABCD` (last 4 chars)
- **Storage:** plaintext in mock; production must use secrets manager (separate concern)
- **PUT with empty token field:** send `"token": "", "tokenRedacted": true` — backend preserves existing
- **Logging:** never log token value; logger strips token field at log boundary

## Validation Strategy

- **Field-level:** Zod schema in `@algotrader/shared`, parsed in:
  - `useSettings()` hook — guards against malformed server response
  - PUT body — client-side check before request
  - Server (mock or real) — re-validates and returns 400
- **Cross-field:** only one — `killSwitchThresholdPct >= maxDrawdownPct` (warn if not)

## Errors & Loading

| State | UI |
|---|---|
| Loading initial | Skeleton placeholders for all 4 sections |
| Save in flight | Save button shows spinner, form disabled |
| Save success | Toast "Сохранено" 2s, form returns to readonly |
| Save network error | Toast "Ошибка. Попробуйте ещё раз.", form remains editable |
| Save 409 conflict | Modal "Настройки изменены. Перезагрузить?" with [Reload] [Cancel] |
| Validation error | Inline red text under field, Save button disabled |

## Out of Scope (deferred)
- Real persistence (currently MSW mock)
- Auth (anyone with URL can edit)
- Audit log
- Encryption at rest
- JSON import/export
