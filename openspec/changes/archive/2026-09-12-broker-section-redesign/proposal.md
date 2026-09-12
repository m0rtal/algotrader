# Proposal: broker-section-redesign

## Why

The current Broker settings section (`apps/web/src/features/settings/sections/BrokerSection.tsx`)
ships with UX defects that make everyday setup painful:

1. **Two save buttons with split semantics** — `Сохранить токен` (writes
   the secret via POST /api/settings/token) and the section-level
   `Сохранить` (writes env/accountId via PUT /api/settings). The user
   cannot tell which button affects which field; a token update is
   silently lost if the user only clicks the section save, and vice
   versa.
2. **Live ↔ Sandbox switch opens a separate confirm dialog** that
   appears a beat after the dropdown selection, forcing the user to
   context-switch between two controls and a modal.
3. **No token reveal toggle** — pasting a 64-character Tinkoff token
   into `type="password"` is a blind operation. Verifying the paste
   succeeded is impossible without re-typing it elsewhere.
4. **Multi-state hint string** — one `<input hint>` cycles through
   `idle | saving | saved | error` on a 3–5 s timeout, hiding
   per-field meaning in a single line.
5. **Defensive guard eats empty submit** — `if (!tokenDraft.trim())
   return` rejects an empty token silently, leaving the user wondering
   why nothing happened.

Concept A from the design exploration
(`/home/hermes/.hermes/profiles/designer/drafts/broker-redesigns.html`,
approved 2026-09-12) addresses all five.

## What Changes

### Frontend (`apps/web`)

- Replace the inline `<SelectField>` for environment with a pair of
  radio cards (`Sandbox` / `Live`) that show selection state and
  per-card risk tag inline.
- When `Live` is selected, render an inline `warn-banner` directly
  beneath the radio group instead of opening a `ConfirmDialog`. The
  user acknowledges the risk by leaving `Live` selected; save then
  commits. (No modal interruption.)
- Add a `<FieldStatus>` status pill (`задан` / `валиден` / `Sandbox`)
  to the right of every field label so each field carries its own
  current state, not one shared hint string.
- Extend `TextField` in `Field.tsx` with an optional `reveal`
  prop that adds an eye-toggle to password inputs. Render a `Показать`
  / `Скрыть` toggle inside the input; preserve tab order and
  accessibility (`aria-pressed` on the toggle).
- Introduce a single `savebar` per section with one primary
  `Сохранить` button that fans out internally:
  - if `tokenDraft` is non-empty → POST `/api/settings/token`
    first, then PUT `/api/settings` with the new `tokenLast4`
    reflected.
  - if `tokenDraft` is empty → PUT `/api/settings` only.
  - both operations share one progress + one result banner.
- Remove the standalone `Сохранить токен` button.
- Validate the token draft on blur (not on every keystroke): format
  check (`t.` prefix, length, charset). Surface errors inline below
  the field, not in the global hint.
- After successful save, show a single result banner summarising the
  effective state (`Токен ••••15A · Account ACC-1234567 · Sandbox ·
  воркер подхватит при следующем запуске`).

### Backend (`apps/api`)

- **No new routes.** The existing endpoints already atomically write
  their respective stores; the change only orchestrates them from the
  frontend.
- `PUT /api/settings` already requires a version stamp and returns
  409 on stale write — kept as-is.

### Canonical spec

- Extend `openspec/specs/settings/spec.md` with a
  `Broker section` capability describing the form contract:
  field composition, status pill meaning, save coordination
  between the two endpoints, and the inline Live warning.

### Test impact

- Update `apps/web/src/features/settings/sections/sections.test.tsx`
  to assert:
  - token reveal toggle changes input `type` from `password` to `text`.
  - selecting `Live` renders the inline `warn-banner` and does
    **not** open any `ConfirmDialog`.
  - clicking `Сохранить` with a token draft issues
    `POST /api/settings/token` followed by `PUT /api/settings` (in
    that order); with empty draft, only the PUT fires.
  - per-field status pills render with the expected text after
    successful save.
- No backend tests change — endpoint contracts are unchanged.

## Impact

- Affected specs: `settings` (one delta adding `Broker section`
  capability).
- Affected code: `apps/web/src/features/settings/sections/BrokerSection.tsx`
  (rewrite), `apps/web/src/features/settings/Field.tsx` (reveal toggle),
  `apps/web/src/features/settings/Section.tsx` (savebar slot for
  per-field status — small adjustment).
- No MSW handler change — both endpoints already mocked.
- No type change in `packages/shared`.
- No `pyproject.toml` / Python change.

## Non-Goals

- Removing the `POST /api/settings/token` endpoint. The separation
  exists on purpose: secrets stay in the SQLite `secrets` table, not
  in the public settings row. The frontend just stops exposing two
  save buttons; the backend keeps its two endpoints.
- Introducing per-field inline edit/cancel — out of scope; the
  section's existing `isDirty` lifecycle is reused.
- A new `GET /api/broker/health` endpoint (concept B) — not part of
  this change.
- A token modal (concept C) — not part of this change.
- Theming-light / dark-mode parity work — separate capability.
- i18n / locale switch — Russian copy stays; translation framework
  not introduced.

## Risks

1. **Two-step save is not transactional.** If `POST /api/settings/token`
   succeeds and `PUT /api/settings` returns 409 (stale version), the
   secret is written but the settings row is not updated. The result
   banner surfaces this: it shows the `tokenLast4` returned by the
   POST and asks the user to retry the PUT manually. Mitigation: the
   UI disables `Сохранить` while either request is in flight so the
   user cannot double-click and race.
2. **Token input spans the autoplay boundary.** Reveal toggle uses
   `aria-pressed`; verified with jsdom + axe-core in CI.
3. **MSW handlers must stay aligned.** Both endpoints remain mocked
   in `apps/web/src/mocks/handlers.ts`; no removal.
4. **Reduced-motion preference is honoured** by the same global
   `@media (prefers-reduced-motion: reduce)` rule already shipped in
   the ui-shell change. The new status pill animation reuses it.
