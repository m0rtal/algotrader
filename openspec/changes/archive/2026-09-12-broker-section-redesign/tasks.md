# Tasks

## 1. Spec delta (settings capability)

- [ ] 1.1 Create `specs/settings/spec.md` (delta) with
  `## ADDED Requirements` header.
- [ ] 1.2 Add requirement `Broker section form contract` with three
  scenarios (save fan-out, live warning inline, token reveal toggle).
- [ ] 1.3 `openspec validate broker-section-redesign --strict` passes.
- [ ] 1.4 Apply delta to canonical `openspec/specs/settings/spec.md`
  (drop `(delta)` suffix, rename `## ADDED Requirements` to
  `## Requirements`, add `## Purpose`).
- [ ] 1.5 `openspec validate settings --strict` passes.
- [ ] 1.6 `openspec archive broker-section-redesign --yes --skip-specs`.

## 2. Field.tsx — reveal toggle

- [ ] 2.1 Add `reveal?: boolean` to `TextFieldProps`.
- [ ] 2.2 When `reveal` is true, render an actions area with one
  `aria-pressed` button (`Показать` / `Скрыть`) that toggles
  `type` between `password` and `text`.
- [ ] 2.3 Optional `clearLabel?: string` to render a second action
  button (`aria-label="Очистить"`) — only used by the token field.
- [ ] 2.4 Verify: pnpm typecheck clean; jsx-a11y clean.

## 3. BrokerSection.tsx — rewrite

- [ ] 3.1 Replace `<SelectField>` for environment with a radio
  cards group; render inline `warn-banner` when `Live` is selected.
- [ ] 3.2 Delete the `<ConfirmDialog open={showLiveConfirm}>` block.
- [ ] 3.3 Pass `reveal` to the token `<TextField>`.
- [ ] 3.4 Replace the standalone `Сохранить токен` button with a
  single section-level `Сохранить` in `savebar` that fans out:
  POST `/api/settings/token` if `tokenDraft` is non-empty,
  then PUT `/api/settings`.
- [ ] 3.5 Add per-field status pills (`задан`, `валиден`, `Sandbox`)
  via the new `<FieldStatus>` component.
- [ ] 3.6 Render a single result banner below `savebar` with the
  effective state after save (ok / 409 / error).

## 4. FieldStatus component

- [ ] 4.1 New `apps/web/src/features/settings/FieldStatus.tsx`
  with `tone: 'ok' | 'warn' | 'err' | 'dim'` and `label: string`.
- [ ] 4.2 Render a small pill with status dot + label, sized to
  align with the field label.
- [ ] 4.3 Mark decorative (`aria-hidden="true"`); the actual field
  state is announced via the field's hint text.

## 5. Tests

- [ ] 5.1 Update `sections.test.tsx`:
  - reveal toggle changes input type from `password` to `text`.
  - selecting `Live` renders `warn-banner` and asserts no
    `ConfirmDialog` opens.
  - save with token draft issues POST then PUT (in order).
  - save with empty draft issues only PUT.
  - status pills render with expected text after save.
- [ ] 5.2 Add a focused test for `Field.tsx` reveal toggle
  (if a dedicated test file does not exist, add to
  `Field.test.tsx` or fold into `ui.test.tsx`).
- [ ] 5.3 `pnpm test` green; coverage ≥95% on the four metrics.

## 6. Validation

- [ ] 6.1 `pnpm lint:a11y` (token + contrast) green.
- [ ] 6.2 `pnpm typecheck` clean.
- [ ] 6.3 `pnpm lint` clean.
- [ ] 6.4 Open `/settings` in dev and walk the three flows by hand:
  sandbox→sandbox, sandbox→live, token paste+reveal, accountId edit.
- [ ] 6.5 `git diff` shows only the files listed in proposal §Impact.
