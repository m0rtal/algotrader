# settings Specification

## ADDED Requirements

### Requirement: Settings Page Access
The system SHALL expose a settings page at the route `/settings` accessible from a gear icon in the topbar of the main dashboard.

#### Scenario: Operator opens settings
- GIVEN the dashboard is loaded
- WHEN the operator clicks the gear icon in the topbar
- THEN the browser navigates to `/settings`
- AND the settings page renders with 4 sub-sections: Broker, Risk, ML, Data

#### Scenario: Operator returns to dashboard
- GIVEN the operator is on `/settings`
- WHEN they click the back button or "← Назад" link
- THEN the browser navigates to `/`
- AND the dashboard state (active tab) is preserved

### Requirement: Broker Settings
The system SHALL provide a form to configure broker connection. The form fields SHALL be: `environment` (sandbox | live), `token` (string), `accountId` (string).

#### Scenario: Display token as masked
- GIVEN the broker section is rendered with a token stored
- WHEN the token input renders
- THEN the displayed value SHALL be a masked form (e.g. `••••••ABCD` showing only the last 4 characters)
- AND the placeholder SHALL be "Paste Tinkoff token here"
- AND the input type SHALL be `password`

#### Scenario: Token PUT uses redacted flag
- GIVEN the operator saves broker settings with token field showing `••••••ABCD`
- WHEN the PUT request is sent
- THEN the request body SHALL include `"token": "", "tokenRedacted": true`
- AND the backend (or MSW handler) SHALL preserve the existing token
- AND the UI SHALL show a success toast

#### Scenario: Environment is sandbox by default
- GIVEN no settings have been saved
- WHEN the broker section is first rendered
- THEN the `environment` select SHALL show "Sandbox" as the default value

#### Scenario: Live environment requires explicit confirmation
- GIVEN the operator changes environment from sandbox to live
- WHEN they click Save
- THEN a confirmation dialog SHALL appear with the text "Включить live-торговлю? Реальные деньги."
- AND the save SHALL only proceed if the operator confirms

### Requirement: Risk Settings
The system SHALL provide a form to configure risk limits. The form fields SHALL be: `maxDrawdownPct` (number 1-50), `maxPositionSizePct` (number 1-100), `killSwitchEnabled` (boolean), `killSwitchThresholdPct` (number 1-50).

#### Scenario: Default risk values
- GIVEN no settings have been saved
- WHEN the risk section is first rendered
- THEN defaults SHALL be: maxDrawdownPct=10, maxPositionSizePct=20, killSwitchEnabled=false, killSwitchThresholdPct=15

#### Scenario: Drawdown validation
- GIVEN the operator enters maxDrawdownPct
- WHEN the value is outside the range 1-50
- THEN the field SHALL show an inline error
- AND the Save button SHALL be disabled

#### Scenario: Kill switch reset requires confirmation
- GIVEN killSwitchEnabled is true and the operator has just saved settings
- WHEN the operator wants to disable the kill switch
- THEN a confirmation dialog SHALL appear: "Отключить kill switch? Риск продолжения убытков."
- AND the change SHALL only apply after confirmation

### Requirement: ML Settings
The system SHALL provide a form to configure ML pipeline behavior. The form fields SHALL be: `modelVersion` (string, readonly, from current model), `retrainIntervalDays` (number 1-90), `confidenceThreshold` (number 0-1), `regimeFilter` (trend | range | vol | all).

#### Scenario: Model version is read-only
- GIVEN the ML section renders
- WHEN the operator views the form
- THEN the modelVersion field SHALL be displayed as readonly text
- AND the value SHALL match the current model's version string (e.g. "v2.3")

#### Scenario: Confidence threshold validation
- GIVEN the operator enters confidenceThreshold
- WHEN the value is outside the range 0-1
- THEN an inline error SHALL appear
- AND the Save button SHALL be disabled

### Requirement: Data Settings
The system SHALL provide a form to configure data source. The form fields SHALL be: `source` (tinkoff | moex_iss | file), `cacheTtlMinutes` (number 1-1440), `historyYears` (number 1-10), `autoFetch` (boolean).

#### Scenario: Default data source is Tinkoff
- GIVEN no settings have been saved
- WHEN the data section is first rendered
- THEN `source` SHALL be "tinkoff"
- AND `cacheTtlMinutes` SHALL be 60
- AND `historyYears` SHALL be 5

#### Scenario: History years affects fetch
- GIVEN the operator sets historyYears to 10
- WHEN they save the data section
- THEN the next fetch.py run SHALL request 10 years of history

### Requirement: Settings Persistence
The system SHALL persist settings server-side. The frontend SHALL fetch settings via `GET /api/settings` and update via `PUT /api/settings`.

#### Scenario: Initial load fetches settings
- GIVEN the settings page is opened
- WHEN the component mounts
- THEN a GET /api/settings request SHALL fire
- AND the form SHALL render with the returned values (or defaults if 404)

#### Scenario: Save round-trip
- GIVEN the operator changes a field
- WHEN they click Save
- THEN a PUT /api/settings request SHALL fire with the full settings object
- AND on success, a toast "Сохранено" SHALL appear for 2 seconds
- AND the form SHALL return to read-only mode

#### Scenario: Concurrent modification warning
- GIVEN the operator opens the page and sees settings version "abc123"
- WHEN another tab updates the settings to version "def456"
- AND the operator tries to save
- THEN the PUT SHALL fail with 409 Conflict
- AND the UI SHALL show "Настройки изменены в другом месте. Перезагрузить."

#### Scenario: Network error shows retry
- GIVEN the PUT request fails with a network error
- WHEN the operator is editing
- THEN the form SHALL remain editable
- AND a toast "Ошибка сохранения. Попробуйте ещё раз." SHALL appear

### Requirement: Danger Zone
The system SHALL provide a danger zone section at the bottom of the settings page with explicit confirmation for destructive actions.

#### Scenario: Reset all settings
- GIVEN the operator scrolls to the danger zone
- WHEN they click "Сбросить настройки"
- THEN a confirmation dialog SHALL appear: "Сбросить все настройки к defaults? Это действие необратимо."
- AND on confirmation, a DELETE /api/settings request SHALL fire
- AND the form SHALL re-render with default values

## MODIFIED Requirements

(none — this is a new spec; stack requirements live in `frontend-shell`)

## REMOVED Requirements

(none)

## Non-Goals (deferred to other changes)
- Auth / multi-user
- Encrypted at-rest storage
- Audit log of setting changes
- Import/export of settings as JSON
- Actual enforcement of risk limits (broker/strategy layer)
