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
- [ ] Reviewer confirms `MOEXFetchOutcome` is a `typing.Literal`
      alias declared in `no_trade_evidence.py` and re-exported
      (NOT re-declared) by `backfill.py`; tests use plain string
      comparisons (`outcome == "complete"`), never
      `MOEXFetchOutcome("complete")`.
- [ ] Reviewer confirms the strict feed contract (HTTP 200, valid
      row lengths, explicit zero counters, explicit null OHLC,
      valid pagination offsets) is enforced at fetch time;
      `FakeResp` test doubles MUST set `status_code = 200` and
      provide row lists whose length matches the declared
      columns.
- [ ] Reviewer confirms `record_historical_no_trade_evidence`
      takes an explicit `ticker` argument; the helper MUST NOT
      guess the ticker from `rows[0].get("_secid")`.
- [ ] Reviewer confirms Phase 2 Task 3 (historical walker
      integration) is MANDATORY, not optional. The walker
      integration is the gate improvement; a helper-only change
      that leaves the in-process walker on the list-only path
      does not satisfy the spec.
- [ ] Reviewer confirms the historical CLI
      `apps/api/scripts/backfill_no_trade_evidence.py` is a thin
      call-through to the new helper (no new argument surface,
      no new exit code path, no new branch).
- [ ] Reviewer confirms the end-to-end test in
      `test_backfill_coverage.py` exercises the real public
      walker with a strict synthetic feed, writes only explicit
      zero-trade evidence (no fake OHLC bars), recomputes the
      canonical `expected_bars` on the TEMP DB via
      `populate_expected_bars`, and asserts that
      `check_coverage([figi])` improves from below 0.95 to at
      or above 0.95 — no manual denominator override, no live
      broker call, no production read.

## Phase 2 — Implementation (separate; planned in
`docs/superpowers/plans/2026-10-03-historical-moex-evidence.md`)

- [ ] Task 1 (mandatory): fetcher outcome contract + RED tests
- [ ] Task 2 (mandatory): historical evidence helper,
      business-date filter, ISIN cross-check, and the
      MANDATORY `BackfillRunner._process_moex_year` /
      `_process_one` historical walk branch integration; the
      historical CLI is a thin call-through to the new helper
- [x] Task 3 (mandatory, end-to-end): the strict-synthetic-feed
      walker test that proves the gate improves on a TEMP DB

## Spec review acceptance criteria (Phase 1)

- [ ] `openspec validate persist-historical-moex-evidence --strict`
      reports `Change 'persist-historical-moex-evidence' is valid`.
- [ ] The two ADDED Requirements each have ≥ 3 GIVEN/WHEN/THEN
      scenarios and reference only public symbols that already
      exist (`_fetch_year_moex`, `record_no_trade_evidence`,
      `_extract_zero_trade_rows`, `_get_meta_moex`,
      `moex_holidays`, `BackfillRunner`,
      `check_coverage`, `populate_expected_bars`) or that
      Phase 2 introduces in the implementation plan
      (`MOEXFetchOutcome`, `record_historical_no_trade_evidence`,
      `_is_business_date_for_evidence`,
      `_fetch_year_moex_outcome`).
- [ ] No scenario references a symbol that is NOT planned in the
      implementation plan (catch scope drift BEFORE the
      implementing subagent dispatches).
- [ ] No new public metric, no new cached `expected_bars` writes
      in production, no new scheduled phase, no CLI argument
      expansion, no claim of full coverage recovery for the 215
      incomplete-history figis.
