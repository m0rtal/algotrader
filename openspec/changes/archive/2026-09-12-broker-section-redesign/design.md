# Design — broker-section-redesign

## Stack

Same as the ui-shell change: React 19 + Vite 6 + Tailwind 4,
React Query for the two backend calls, no new dependencies.

## Layout

```
Section [Брокер]
├── header
│   ├── title + description
│   └── status pill (top-right, mirrors per-field state)
├── field: Environment
│   ├── label · label-row with "текущее: Sandbox" hint
│   ├── radio cards (2) — Sandbox / Live
│   └── inline warn-banner when Live selected (no modal)
├── field: Token
│   ├── label · status pill "задан" / "не задан"
│   ├── TextField type=password with reveal toggle + clear button
│   ├── inline hint: format / length / current last4
│   └── inline error if validation fails (on blur)
├── field: Account ID
│   ├── label · status pill "валиден" / "пусто"
│   └── inline hint: matched-with-token or "не проверен"
└── savebar (single)
    ├── left: "Изменений нет · последний save N мин назад" or saving spinner
    ├── right: [Отменить] [Сохранить]
    └── below: result banner after save (ok | err)
```

The two existing backend endpoints stay unchanged; the frontend
orchestrates them inside the single save handler.

## Data Flow

```
User clicks Сохранить
   │
   ├─ tokenDraft trimmed? ─ yes ─► POST /api/settings/token
   │                                 │
   │                                 ├─ ok: read tokenLast4 from response
   │                                 │      push into local BrokerSettings draft
   │                                 │
   │                                 └─ err: result banner "Ошибка записи токена: …"
   │
   └─ PUT /api/settings  (always, with the (possibly updated) draft)
        │
        ├─ ok: result banner "Сохранено: токен ••••15A · Sandbox"
        │
        └─ 409: result banner "Конфликт версии, обновите и повторите"
```

No new state shape. `BrokerSettings` (defined in
`packages/shared/src`) is unchanged.

## Component Changes

- `Field.tsx` — add optional `reveal?: boolean` to `TextFieldProps`;
  when true, render an action area to the right of the input with
  two buttons: `aria-pressed` reveal toggle and clear (×) for the
  token field only.
- `Section.tsx` — already exposes a save slot; no API change.
- `BrokerSection.tsx` — full rewrite per the layout above. The
  standalone `useSaveToken` hook stays; its semantics are unchanged
  (single POST).

## Token validation

- On blur, the token field runs a synchronous format check:
  - starts with `t.` (Tinkoff convention)
  - length is within the documented bounds (≥20, ≤512)
  - charset is `[A-Za-z0-9._-]`
- Errors render inline below the field, in red. The save button stays
  disabled until the draft is either empty (no change) or passes
  validation.
- No round-trip validation against Tinkoff API in the UI — that
  already exists in `/admin/backfill/start` and reports in the
  global log strip.

## Accessibility

- Each field retains its `<label htmlFor>` link.
- Status pill is `role="status"` with `aria-live="polite"` only on
  the result banner; the per-label pills are decorative (icon + text
  always visible).
- Reveal toggle button is `type="button"` `aria-pressed={visible}` and
  has `aria-label="Показать токен"` / `"Скрыть токен"` toggled by
  state.
- Radio cards behave as a radiogroup via `role="radiogroup"` on the
  container and `role="radio" aria-checked` on each card; arrow-key
  navigation handled by a small `onKeyDown`.
- The warn-banner uses `<aside role="note" aria-label="Предупреждение">`
  so screen readers announce it once when Live is selected.

## Risks

1. **Non-atomic save.** Documented in proposal.md §Risks.
2. **MSW passthrough parity.** No endpoint changes; no MSW change.
3. **Coverage gate (≥95%).** The new branches (reveal toggle,
   validation on blur, fan-out save handler) add uncovered code
   paths — covered by the test additions listed in proposal.md.
4. **Visual regression on existing screenshots.** The Section's
   outer chrome and savebar slot are unchanged; only internal
   composition shifts. Existing SettingsTab / Sections tests
   asserting `data-testid` attributes remain green.
