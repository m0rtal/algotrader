# Verified delisting metadata design

## Scope and Stack

Use the existing Python 3.11+, requests session, date parser, SQLite connection and writer lock. The only implementation owner is the historical no-trade CLI. No new dependency, table, service, broker API or generalized metadata architecture.

## Current Flow and Fault

`main` selects stale rows, `_get_meta_moex` tries a primary board, and `_probe_board_last(ticker)` selects a latest date without status or identity validation. Lines 193–230 commit that date under the listed-till lock. Only after the year fetch, complete outcome and non-empty zero rows does lines 292–321 probe ISIN. Lines 233–235 can skip that guard entirely. `fetch_issuer_identity` returns None when all boards are inactive; it cannot establish inactive-board identity. The probe must read identity directly from the successful securities metadata description, not fabricate it from the local row.

## Minimal Interface and Data Flow

Change the CLI-private `_probe_board_last(ticker: str)` return from `tuple[str, str] | None` to `tuple[str, str, str] | None` containing board, strict date and verified upstream ISIN. No public helper contract changes. Validate HTTP 200 and entire description/boards shape before returning; exact description SECID must equal requested ticker. Required description columns are `name` and `value`; required board columns are `secid`, `boardid`, `is_traded`, `listed_till`. Reject duplicate columns/identity entries and any short/long row. Every board row must have a non-empty string `secid` exactly equal to the requested ticker as well as a non-empty board identifier and a calendar-valid exact ISO end. Inactive forms are exactly integer `0` (bool excluded from that integer branch), boolean `False`, and exact string `"0"`. Active forms are exactly integer `1`, boolean `True`, and exact string `"1"`; each prevents delisting, including on a non-primary board. Every other form is invalid/unknown, including `0.0`, `1.0`, `""`, `None`, `" 0"`, `"0 "`, `"false"`, `"true"`, `"unknown"`, and integer `2`. Never use general truthiness or integer coercion. Any active board prevents delisting; any absent end leaves completeness unknown. Pick the maximum end across all validated inactive boards using the existing ordering.

At the caller, require both local and upstream ISIN non-empty and equal before selecting delisting and before any UPDATE/history call. Retain `lt < last_session` and `max_ts <= lt_iso`. Carry that same upstream ISIN to the historical evidence helper for the inactive path instead of re-probing through a primary-board-only function. The still-listed evidence path keeps its existing identity validation. This is a local trust-boundary correction, not a change to active metadata routing.

## Rulings

1. Successful metadata identity and complete inactive boards are required even if there is no history window. Missing history or bar counts do not prove delisting.
2. A genuine matched delisting fact can commit without history. Subsequent history partial/error/malformed/identity failure does not roll it back. Those failures still prohibit evidence and cannot change bars/expected-bars in this CLI.
3. Metadata rejection preserves prior state exactly, including a pre-existing non-null listed-till. Do not clear or repair previously stored production values in this fix.
4. Equal-last-bar valid metadata may remove staleness under canonical gate rules. Equal-last-bar invalid metadata must remain stale. The cached denominator and 0.95 ratio are unchanged.
5. Shared writer transactions stay separate; no network or sleep under locks. Rollback pending failed mutation before release; close CLI-owned connection in finally. Acquisition/SQLite BUSY retains exit 75; ordinary degraded work remains exit 0. Dry-run remains lock-free/read-only.

## Risks and Verification

Fail-closed validation can reject unfamiliar or incomplete upstream shapes; this is preferable to marking an unrelated instrument ready. Fixtures must use complete realistic columns and description identity, not tuple-only mocks that skip the boundary. Retain foreign-bar-after-end protection. Use real CLI `main`, actual SQL readback and actual coverage; metadata/history transports alone are offline fakes. For partial history, return one valid row with cursor columns `INDEX`, `TOTAL`, `PAGESIZE` and data `[[0, 2, 2]]`; the actual `_fetch_year_moex_outcome` parser must produce one retained row, `partial`, and exactly one HTTP call. Do not mock that outcome function. Freeze module-local date bindings, not shared stdlib objects. Capture RED semantics, then GREEN and full backend coverage ≥95%, independent review, CI, approved merge and backup-first deployment in the later execution flow.

## Authority

Additive delta targets canonical `openspec/specs/data-quality/spec.md`, notably Pre-Consumption Coverage Gate and Expected Bars Caching. Retain canonical writer-coordination Shared Market-Data Lock Namespace, Write-Section-Only Scope, Separate Evidence Transactions and Existing Dry-Runs Stay Read-Only. Retain historical-evidence change's complete fetch, exact window, explicit zero and identity clauses. Its unarchived location is recorded, not silently treated as a canonical file. No canonical edits or archive in this docs-only pass.
