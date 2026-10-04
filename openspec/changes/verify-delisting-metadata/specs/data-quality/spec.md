# Data quality Specification (delta)

## ADDED Requirements

### Requirement: Verified Delisting Metadata Before Mutation

The historical no-trade CLI SHALL persist `instruments.listed_till` only after validating the successful HTTP 200 metadata response for the requested ticker, its exact description SECID and a non-empty upstream ISIN equal to the non-empty local `instruments.isin`. It SHALL validate the complete description and boards containers, unique required column names, every row's exact column length, a required `secid` column with a non-empty string exactly equal to the requested ticker in every board row, non-empty board identifiers, and explicit `is_traded` forms (inactive: exact integer `0`, boolean `False`, exact string `"0"`; active: exact integer `1`, boolean `True`, exact string `"1"`; bool is not an integer form), and every board's exact calendar-valid `YYYY-MM-DD` listing end before selecting the latest board end. Missing/null dates, truncated dates, malformed containers or rows, missing/ambiguous identity, HTTP failure and network/JSON failure SHALL be unknown, not delisting. Every other activity form, including floats `0.0`/`1.0`, empty/null values, whitespace-padded strings and unknown strings, SHALL be invalid/unknown; general truthiness or integer coercion SHALL NOT be used. All boards must be inactive; absence of a primary board alone SHALL NOT establish inactivity. The latest date must be strictly earlier than the last completed MOEX business session. Local bars after that date SHALL retain the existing foreign-security guard and prevent mutation. No threshold, expected-bars formula, instrument universe or coverage-gate semantics SHALL change.

Validation SHALL precede the first mutation and the first historical request, including when `lo > win_hi` and there is no historical window. Rejected metadata SHALL leave the prior `listed_till` (including an existing non-null value), all bars, cached `expected_bars`, and evidence rows exactly unchanged for that FIGI. Missing local bars, missing historical rows and zero bar counts SHALL NOT be delisting evidence. Validated metadata is an independent fact: it SHALL NOT require synthetic zero-trade rows, a primary active board, or subsequent history success. A later incomplete, erroneous, malformed or identity-mismatched historical response SHALL NOT roll back separately committed verified metadata; it SHALL leave bars, cached expected-bars and evidence unchanged on this read-only-bar CLI path. Historical evidence remains subject to the existing complete-fetch, explicit-zero, identity, business-date and actual-window contracts.

The existing shared `no-trade-evidence/listed-till` and `no-trade-evidence/evidence` locks SHALL remain separate and non-nested. Metadata/history I/O and sleep SHALL occur outside locks. Pending mutation failure SHALL attempt rollback before lock release, retain original exception semantics, and close the CLI-owned connection. Existing busy deferral exits 75; ordinary rejected/degraded work exits 0 with a bounded non-secret diagnostic; dry-run acquires no writer lock and changes no SQLite state.

#### Scenario: Wrong or missing metadata identity is rejected before history

- GIVEN a stale FIGI and a metadata response with SECID `SBER` for requested `GAZP`, mismatched ISIN, missing SECID, empty upstream ISIN, empty local ISIN, missing boards `secid` column, or any empty/mismatched board-row `secid` (even when description SECID is `GAZP`)
- WHEN the actual historical CLI evaluates that FIGI
- THEN it SHALL perform neither a listed-till update nor a historical request for that rejected candidate
- AND rejection SHALL apply with both a non-empty history window and `lo > win_hi`
- AND its prior listed-till, bars, expected-bars and evidence SHALL remain exactly unchanged
- AND it SHALL exit 0 and emit a bounded rejection diagnostic

#### Scenario: HTTP and malformed metadata cannot certify delisting

- GIVEN a stale FIGI and HTTP 500 carrying a plausible JSON body, a network/JSON error, an incomplete board row, duplicate/missing required columns, malformed container, absent date, invalid date or non-ISO date suffix
- WHEN the CLI probes delisting metadata
- THEN it SHALL reject the entire metadata candidate without an unchecked parser exception or mutation
- AND an active board, including a non-primary active board, SHALL prevent a delisting conclusion

#### Scenario: Invalid metadata cannot make an empty history window ready

- GIVEN `MAX(bars.ts) = 2026-09-10`, `expected_bars = 1`, no evidence and last completed session `2026-10-01`
- AND unverified metadata proposes listed-till `2026-09-10`, making `lo > win_hi`
- WHEN the CLI runs and actual `check_coverage(conn, [figi])` is read before and after
- THEN it SHALL preserve exact SQLite state and the stale failure
- AND it SHALL not make readiness pass merely by shortening the effective end

#### Scenario: Valid inactive metadata commits without historical rows

- GIVEN matched SECID and non-empty ISIN in HTTP 200 metadata with a complete well-formed inactive board set
- AND its latest listed-till `2026-09-10` is before last completed session `2026-10-01` and equals the last local bar
- WHEN the actual CLI runs twice
- THEN it SHALL persist verified listed-till `2026-09-10` without requesting an empty historical window
- AND the second run SHALL leave the first run's SQL state unchanged
- AND bars, expected-bars and evidence SHALL remain unchanged
- AND actual coverage SHALL retain canonical delisting behavior and the 0.95 completeness threshold

#### Scenario: Verified metadata remains independent of degraded history

- GIVEN valid inactive identity-matched metadata and a non-empty historical window
- WHEN the first historical request observes the database and then history returns partial, HTTP error, malformed data, wrong SECID/BOARDID or raises a network exception
- AND the partial-history acceptance fixture SHALL use the real parser with one valid history row, cursor columns `INDEX`, `TOTAL`, `PAGESIZE` and data `[[0, 2, 2]]`, producing `partial`, one retained row and exactly one HTTP call through transport-only fakes
- THEN metadata identity SHALL already have been validated before that request and any listed-till mutation
- AND verified listed-till SHALL remain committed
- AND the CLI SHALL add no evidence and change no bars or cached expected-bars
- AND ordinary degraded work SHALL retain exit 0 and existing bounded diagnostics

#### Scenario: Exact activity forms are classified without coercion

- GIVEN identity-matched complete HTTP 200 metadata
- WHEN every board uses inactive exact integer `0`, boolean `False` or exact string `"0"`
- THEN those activity forms SHALL permit verified delisting subject to all other guards
- AND any active exact integer `1`, boolean `True` or exact string `"1"` on any board SHALL prevent delisting
- AND any other activity form, including `0.0`, `1.0`, `""`, null, `" 0"`, `"0 "`, `"false"`, `"true"`, `"unknown"` or integer `2`, SHALL fail closed without changing prior state

#### Scenario: Foreign bars and future listing retain guards

- GIVEN valid metadata but a local bar later than its latest board end, or a board end on/after the last completed session
- WHEN the CLI evaluates the candidate
- THEN it SHALL not store that date as delisting
- AND all prior FIGI state SHALL remain unchanged

#### Scenario: Contention and dry-run retain transaction ownership

- GIVEN a validated candidate on a file-backed temporary SQLite database
- WHEN listed-till acquisition or SQLite mutation is busy
- THEN pending mutation SHALL not survive and rollback SHALL precede unlock when a transaction started
- AND the CLI SHALL close its owned connection and exit 75 with one bounded deferral line
- AND a later evidence busy event SHALL not undo already committed verified listed-till
- AND dry-run SHALL preserve exact SQL state without acquiring either writer lock
