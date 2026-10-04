# Tasks
## Phase 1 — Spec review only (this change)

- [x] Spec reviewer validates `openspec validate
      persist-historical-moex-evidence --strict` and confirms every
      ADDED Requirement has ≥1 GIVEN/WHEN/THEN scenario that maps to
      a reachable function in the design (no dead assertions).
- [x] Reviewer confirms the change touches exactly one capability
      (`data-quality`); no MODIFIED / REMOVED Requirements; cached
      `expected_bars` / 95 % gate / staleness override / TTL
      semantics unchanged.
- [x] Reviewer confirms no production code or migration lands in
      this change folder; only `proposal.md`, `design.md`,
      `tasks.md`, and `specs/data-quality/spec.md`.
- [x] Reviewer confirms `MOEXFetchOutcome` is a `typing.Literal`
      alias declared in `no_trade_evidence.py` and re-exported
      (NOT re-declared) by `backfill.py`; tests use plain string
      comparisons (`outcome == "complete"`), never
      `MOEXFetchOutcome("complete")`.
- [x] Reviewer confirms the strict feed contract (HTTP 200, valid
      row lengths, explicit zero counters, explicit null OHLC,
      valid pagination offsets) is enforced at fetch time;
      `FakeResp` test doubles MUST set `status_code = 200` and
      provide row lists whose length matches the declared
      columns.
- [x] Reviewer confirms `record_historical_no_trade_evidence`
      takes an explicit `ticker` argument; the helper MUST NOT
      guess the ticker from `rows[0].get("_secid")`.
- [x] Reviewer confirms Phase 2 Task 3 (historical walker
      integration) is MANDATORY, not optional. The walker
      integration is the gate improvement; a helper-only change
      that leaves the in-process walker on the list-only path
      does not satisfy the spec.
- [x] Reviewer confirms the historical CLI
      `apps/api/scripts/backfill_no_trade_evidence.py` is a thin
      call-through to the new helper (no new argument surface,
      no new exit code path, no new branch).
- [x] Reviewer confirms the end-to-end test in
      `test_backfill_coverage.py` exercises the real public
      walker with a strict synthetic feed, writes only explicit
      zero-trade evidence (no fake OHLC bars), recomputes the
      canonical `expected_bars` on the TEMP DB via
      `populate_expected_bars`, and asserts that
      `check_coverage(conn, [figi])` improves from below 0.95 to at
      or above 0.95 — no manual denominator override, no live
      broker call, no production read.

## Phase 2 — Implementation (separate; planned in
`docs/superpowers/plans/2026-10-03-historical-moex-evidence.md`)

- [x] Task 1 (mandatory): fetcher outcome contract + RED tests
- [x] Task 2 (mandatory): historical evidence helper,
      business-date filter, ISIN cross-check, and the
      MANDATORY `BackfillRunner._process_moex_year` /
      `_process_one` historical walk branch integration; the
      historical CLI is a thin call-through to the new helper
- [x] Task 3 (mandatory, end-to-end): the strict-synthetic-feed
      walker test that proves the gate improves on a TEMP DB

## Spec review acceptance criteria (Phase 1)

- [x] `openspec validate persist-historical-moex-evidence --strict`
      reports `Change 'persist-historical-moex-evidence' is valid`.
- [x] The two ADDED Requirements each have ≥ 3 GIVEN/WHEN/THEN
      scenarios and reference only public symbols that already
      exist (`_fetch_year_moex`, `record_no_trade_evidence`,
      `_extract_zero_trade_rows`, `_get_meta_moex`,
      `moex_holidays`, `BackfillRunner`,
      `check_coverage`, `populate_expected_bars`) or that
      Phase 2 introduces in the implementation plan
      (`MOEXFetchOutcome`, `record_historical_no_trade_evidence`,
      `_is_business_date_for_evidence`,
      `_fetch_year_moex_outcome`).
- [x] No scenario references a symbol that is NOT planned in the
      implementation plan (catch scope drift BEFORE the
      implementing subagent dispatches).
- [x] No new public metric, no new cached `expected_bars` writes
      in production, no new scheduled phase, no CLI argument
      expansion, no claim of full coverage recovery for the 215
      incomplete-history figis.

## Completion evidence — 2026-10-03

Acceptance is closed for this change, not for production readiness. The
controller supplied the approved independent Task 1–3, final spec/quality,
whole-branch fixes, verification-test-wave, and SDK-budget reviews. Final
validated source HEAD: `e1ca79edec7310757e89d469679c47707665de85`.

The delta contains two ADDED Requirements with 11 and 14 scenarios,
respectively; no MODIFIED or REMOVED Requirements. The change folder holds
specification documents and existing `.openspec.yaml` metadata, not source
or migrations. The active plan's final-fix ruling supersedes older
illustrative signatures: the helper requires explicit `from_d`/`to_d`.
The legacy fetcher remains list-only; `_fetch_year_moex_outcome` carries
provenance. `check_coverage(conn, [figi])` returns a failing list, so `[]`
means acceptance; it does not return an `ok=True` object.

The independently reviewed real-walker TEMP-DB test observes PRE `2/2810`
with reason `incomplete`, POST `2/2`, 2808 explicit-zero evidence rows,
and `check_coverage(conn, [figi]) == []`. It uses the real
`populate_expected_bars` before and after evidence, not the older surrogate
or a manual denominator. CLI partial-outcome re-review confirms exit `0`
and zero evidence without a new CLI branch or exit path.

Whole-backend results, exact command, exclusions, skips, and limitations:
[`2026-10-03-historical-moex-evidence-validation.md`](../../../docs/superpowers/results/2026-10-03-historical-moex-evidence-validation.md).
