# Tasks

## Phase 1 — Spec review only (this change)

- [ ] Spec reviewer validates `openspec validate
      persist-historical-moex-evidence --strict` and confirms every
      ADDED Requirement has ≥1 GIVEN/WHEN/THEN scenario that maps to
      a reachable function in the design (no dead assertions).
- [ ] Reviewer confirms the change touches exactly one capability
      (`data-quality`); no MODIFIED / REMOVED Requirements; cached
      `expected_bars` / 95 % gate / staleness override / TTL
      semantics unchanged.
- [ ] Reviewer confirms no production code or migration lands in
      this change folder; only `proposal.md`, `design.md`,
      `tasks.md`, and `specs/data-quality/spec.md`.

## Phase 2 — Implementation (separate; planned in
`docs/superpowers/plans/2026-10-03-historical-moex-evidence.md`)

- [ ] Task 1: fetcher outcome contract + RED tests
- [ ] Task 2: historical evidence helper, business-date filter,
      CLI + identity gate
- [ ] Task 3 (optional, independently reviewable): end-to-end
      consumer regression — the historical walker in
      `BackfillRunner._process_moex_year` and `_process_one` uses
      the new outcome gate; bar-list consumer behaviour unchanged.

## Spec review acceptance criteria (Phase 1)

- [ ] `openspec validate persist-historical-moex-evidence --strict`
      reports `Change 'persist-historical-moex-evidence' is valid`.
- [ ] The two ADDED Requirements each have ≥ 3 GIVEN/WHEN/THEN
      scenarios and reference only public symbols that already
      exist (`_fetch_year_moex`, `record_no_trade_evidence`,
      `record_no_trade_evidence`, `_extract_zero_trade_rows`,
      `_get_meta_moex`, `moex_holidays`) or that Phase 2 introduces
      in the implementation plan
      (`MOEXFetchOutcome`, `record_historical_no_trade_evidence`,
      `_is_business_date_for_evidence`).
- [ ] No scenario references a symbol that is NOT planned in the
      implementation plan (catch scope drift BEFORE the
      implementing subagent dispatches).
- [ ] No new public metric, no new cached `expected_bars` writes,
      no new scheduled phase.