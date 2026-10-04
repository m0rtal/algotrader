# Verify delisting metadata

## Why

At base `ad58b28`, the historical CLI commits `listed_till` before checking upstream identity. `_probe_board_last` ignores HTTP status and description identity, truncates dates and trusts board row shapes. Its empty-window branch bypasses the later history/identity checks entirely. The independently supplied 13-case CLI proof includes `equal_last_bar_adversarial`: HTTP 500 metadata makes canonical coverage change from stale to ready without new bars or evidence.

## What Changes

Add one data-quality requirement for verified delisting metadata before mutation. Keep valid metadata independent of subsequent history success. Specify rejection preservation, empty-window behavior and retained lock/exit/dry-run contracts. Implement later as one narrow CLI fix with executable TDD regressions; this commit is documentation only.

## Impact

Future code surface: `apps/api/scripts/backfill_no_trade_evidence.py`; one focused CLI test file and existing lock fixtures if their stubs must carry newly required identity. No schema migration or public CLI/API change. Authority: `openspec/specs/data-quality/spec.md` and `openspec/specs/writer-coordination/spec.md`; historical evidence clauses remain in `openspec/changes/persist-historical-moex-evidence/specs/data-quality/spec.md`. Preserve all existing clauses; do not rewrite or archive those changes.

## Non-Goals

No new architecture, broker API, global metadata refactor, mutable denominator refresh, production cleanup, source/test changes in this documentation pass, push, merge or deploy. This delta does not promise that verified delisting alone satisfies the completeness ratio or proves seven days of autonomous operation.

## Evidence and Plan

- `docs/superpowers/evidence/verified-delisting-metadata/preflight.md`
- `docs/superpowers/evidence/verified-delisting-metadata/progress.md`
- `docs/superpowers/plans/2026-10-04-verified-delisting-metadata.md`
