# Tasks: add-settings

## 1. Shared types
- [ ] 1.1. Add `SettingsSchema` Zod schema to `packages/shared/src/index.ts` (broker + risk + ml + data sections)
- [ ] 1.2. Export `Settings` type
- [ ] 1.3. Add `DEFAULT_SETTINGS` constant
- [ ] 1.4. Add `REDACTED_TOKEN` sentinel for `••••••ABCD` masking

## 2. Mock data
- [ ] 2.1. Add `settings` mock to `apps/web/src/mocks/data.ts` (sandbox env, masked token, default risk/ML/data)
- [ ] 2.2. Add 3 MSW handlers in `apps/web/src/mocks/handlers.ts`:
  - `GET /api/settings` → 200 with mock
  - `PUT /api/settings` → 200 with updated body + new version hash
  - `DELETE /api/settings` → 204
- [ ] 2.3. Mock concurrent edit: 409 when PUT `version` doesn't match server's stored version (track in handler)

## 3. API client
- [ ] 3.1. Add `useSettings()` TanStack Query hook in `apps/web/src/lib/hooks.ts`
- [ ] 3.2. Add `useSaveSettings()` mutation hook with optimistic update
- [ ] 3.3. Add `useDeleteSettings()` mutation hook

## 4. Settings store
- [ ] 4.1. Create `apps/web/src/stores/settingsStore.ts` (Zustand)
  - State: `values`, `baseline`, `saving`, `error`
  - Actions: `setValues`, `update`, `isDirty`, `save`, `reset`
- [ ] 4.2. Unit test: all actions

## 5. Shared components
- [ ] 5.1. Create `features/settings/ConfirmDialog.tsx` (modal with title, body, confirm/cancel)
- [ ] 5.2. Create `features/settings/Field.tsx` (label + input + error slot, typed for text/number/bool/select)
- [ ] 5.3. Create `features/settings/Section.tsx` (title + grid of Fields + Save button)

## 6. Settings sections
- [ ] 6.1. `features/settings/sections/BrokerSection.tsx` (env select, token password input with masked display, accountId text)
- [ ] 6.2. `features/settings/sections/RiskSection.tsx` (drawdown number, position size number, kill switch toggle, kill switch threshold)
- [ ] 6.3. `features/settings/sections/MLSection.tsx` (model version readonly, retrain interval, confidence threshold, regime filter)
- [ ] 6.4. `features/settings/sections/DataSection.tsx` (source select, cache TTL, history years, auto-fetch toggle)

## 7. Settings page
- [ ] 7.1. `features/settings/SettingsTab.tsx` — sub-tab navigation (Broker/Risk/ML/Data), renders active section
- [ ] 7.2. `features/settings/DangerZone.tsx` — Reset all + Arm live toggles
- [ ] 7.3. `pages/Settings.tsx` — header with back link + SettingsTab
- [ ] 7.4. Add `/settings` route to `app/router.tsx` (fallback to Dashboard for unknown)

## 8. Navigation
- [ ] 8.1. Add gear icon (⚙) to `components/layout/Topbar.tsx` linking to `/settings`
- [ ] 8.2. Add back arrow (← Назад) in Settings header

## 9. Tests
- [ ] 9.1. `stores/settingsStore.test.ts` — actions
- [ ] 9.2. `lib/hooks.test.tsx` — extend with useSettings/useSaveSettings/useDeleteSettings
- [ ] 9.3. `features/settings/sections/BrokerSection.test.tsx` — token masking, live confirm
- [ ] 9.4. `features/settings/sections/RiskSection.test.tsx` — validation, kill switch confirm
- [ ] 9.5. `features/settings/sections/MLSection.test.tsx` — readonly, validation
- [ ] 9.6. `features/settings/sections/DataSection.test.tsx` — defaults, history years
- [ ] 9.7. `features/settings/DangerZone.test.tsx` — reset confirm
- [ ] 9.8. `pages/Settings.test.tsx` — integration: load + edit + save flow
- [ ] 9.9. `components/layout/Topbar.test.tsx` — extend with gear icon link
- [ ] 9.10. `mocks/handlers.test.ts` — 200/409/204 paths

## 10. Lint + typecheck + coverage
- [ ] 10.1. `pnpm typecheck` clean
- [ ] 10.2. `pnpm test` 100% pass
- [ ] 10.3. Coverage ≥95% on all 4 metrics (Vitest threshold enforced)
- [ ] 10.4. `pnpm build` succeeds

## 11. Documentation
- [ ] 11.1. Update root `README.md` — mention `/settings` route
- [ ] 11.2. ADR auto-updates via post-commit hook (no manual work)

## 12. Archive
- [ ] 12.1. `openspec archive add-settings --yes` (after all tasks done + green CI)
- [ ] 12.2. Verify `openspec/specs/settings/spec.md` is canonical

## Out of scope
- Real backend persistence (separate change `add-backend-api`)
- Auth (separate change)
- Audit log
- Encrypted at-rest storage
- JSON import/export
