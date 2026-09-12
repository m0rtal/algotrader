# settings Specification (delta)

## ADDED Requirements

### Requirement: Broker section form contract

The Broker section SHALL expose the three broker fields
(`environment`, `token`, `accountId`) inside one Section that uses a
single save action. The form SHALL coordinate two backend endpoints
without exposing two save buttons.

The section SHALL replace the legacy separate
`<ConfirmDialog>` for environment change with an inline `warn-banner`
that appears whenever `environment === 'live'` is selected.

The token input SHALL offer a keyboard-accessible reveal toggle
(`aria-pressed`) so the operator can verify a pasted token without
leaving the page.

#### Scenario: Save with token draft

- **GIVEN** the operator has pasted a token into the token field
  **AND** the format passes the inline check (`t.` prefix, length,
  charset)
- **WHEN** the operator clicks `Сохранить`
- **THEN** the frontend issues `POST /api/settings/token` first
- **AND** on success, issues `PUT /api/settings` with the new
  `tokenLast4` reflected
- **AND** a single result banner shows
  `Токен ••••last4 · Account ACC-… · Sandbox · воркер подхватит при следующем запуске`.

#### Scenario: Save with empty token draft

- **GIVEN** the operator has changed `environment` and/or `accountId`
  **AND** the token field is empty (no change intended)
- **WHEN** the operator clicks `Сохранить`
- **THEN** the frontend issues only `PUT /api/settings`
- **AND** the existing broker token in the SQLite `secrets` table is
  preserved untouched.

#### Scenario: Reveal toggle changes input type

- **GIVEN** the token field is rendered with `type="password"`
- **WHEN** the operator clicks the reveal toggle
- **THEN** the input `type` becomes `text`
- **AND** the toggle's `aria-pressed` becomes `true`
- **AND** the toggle's `aria-label` becomes `Скрыть токен`.

#### Scenario: Live selection shows inline warning

- **GIVEN** the broker section is rendered
- **WHEN** the operator selects `Live` from the environment radio
  cards
- **THEN** an inline `warn-banner` appears beneath the cards with
  text `Live — реальные деньги. Проверьте risk-лимиты во вкладке «Риск» перед включением.`
- **AND** no `ConfirmDialog` opens.
- **WHEN** the operator switches back to `Sandbox`
- **THEN** the warn-banner disappears.

## MODIFIED Requirements

### Requirement: Broker Settings

The system SHALL provide a form to configure broker connection. The
form fields SHALL be: `environment` (sandbox | live, selected via
radio cards), `token` (string, with reveal toggle and clear button),
`accountId` (string, validated against Tinkoff when token is set).

#### Scenario: Display token as masked

- **GIVEN** the broker section is rendered with a token stored
- **WHEN** the token input renders
- **THEN** the displayed value SHALL be a masked form
  (`t.••••••••15A`) showing only the last 4 characters
- **AND** the placeholder SHALL be `Вставьте токен Tinkoff`
- **AND** the input type SHALL default to `password`
- **AND** a reveal toggle button SHALL be present and keyboard
  accessible.

#### Scenario: Token PUT uses redacted flag

- **GIVEN** the operator saves broker settings with token field empty
  (no change)
- **WHEN** the PUT request is sent
- **THEN** the request body SHALL include `"token": ""`,
  `"tokenRedacted": true`
- **AND** the backend (or MSW handler) SHALL preserve the existing
  token
- **AND** the UI SHALL show a success toast.

#### Scenario: Environment is sandbox by default

- **GIVEN** no settings have been saved
- **WHEN** the broker section is first rendered
- **THEN** the `Sandbox` radio card SHALL show the selected state.

#### Scenario: Live environment requires explicit confirmation

- **GIVEN** the operator changes environment from sandbox to live
- **WHEN** the radio card selection changes
- **THEN** an inline `warn-banner` SHALL appear with the text
  `Live — реальные деньги.`
- **AND** clicking `Сохранить` SHALL proceed directly (no confirm
  dialog step).
