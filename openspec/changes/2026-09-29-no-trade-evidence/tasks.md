# Tasks

- [ ] Apply migration 025 (manual under backup; per AGENTS.md).
- [ ] Wire `_fetch_year_moex` to thread `_secid`, `_boardid`,
      `_numtrades`, `_value` (PR commit #1).
- [ ] Add `no_trade_evidence` module with `record_no_trade_evidence`,
      `reconcile_no_trade_evidence`, `load_no_trade_dates`,
      `_extract_zero_trade_rows` (PR commit #1).
- [ ] Wire `BackfillRunner.backfill_moex_recent_tail` to record
      evidence before writing bars (PR commit #1).
- [ ] Wire `replace_bars_for_figi` to reconcile evidence after every
      commit (PR commit #1).
- [ ] Wire `check_coverage` staleness override with continuous evidence
      chain (PR commit #1).
- [ ] Tests: - `_extract_zero_trade_rows` filters partial / wrong-shape rows. - `record_no_trade_evidence` skips dates that already have a real
      bar; ON CONFLICT refreshes `observed_at` and `expires_at`. - `reconcile_no_trade_evidence` removes rows whose date now has a
      bar. - `load_no_trade_dates` excludes expired rows. - `check_coverage` accepts a continuous chain and rejects a
      partial chain. - `_fetch_year_moex` exposes the new raw fields on every emitted
      dict (regression test). - `backfill_moex_recent_tail` records evidence before writing
      bars (integration test against a temp SQLite).
