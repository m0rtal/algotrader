# Verified delisting metadata progress

## Documentation Gate

- [x] Confirm isolated worktree, branch `fix/verified-delisting-metadata` and base `ad58b28a96f15b85984a228dcdb26f34ccf515fe`; initial worktree clean.
- [x] Read actual CLI, metadata/identity helpers, actual coverage signature, existing file-backed lock fixtures, coverage configuration and canonical authorities.
- [x] Aggregate all 13 independently supplied actual CLI records; bars and cached expected-bars unchanged in all 13; evidence added 0; decisive adversarial stale-to-ready recorded in `preflight.md`.
- [x] Record identity rulings: verified HTTP200 SECID/non-empty matching ISIN and complete inactive boards before mutation/history; empty-window rejection; genuine delisting independent of subsequent history; retained foreign/future guards and canonical denominator/threshold.
- [x] Create change with installed OpenSpec 1.13.0 CLI; `.openspec.yaml` auto-generated, not hand-edited.
- [x] Write proposal/design/tasks and additive data-quality delta; no canonical apply/archive.
- [x] Write plan with one implementation task and release verification, real CLI/SQL scaffold, RED-before-GREEN gates, ≥95% full backend coverage, independent review, CI/operator-or-cron merge and backup-first deployment.
- [x] Verify five Python fenced blocks compile (caller fragment compiled in its documented loop context); seven existing regression-file paths exist; no TBD/TODO markers.
- [x] Verify three authority files byte-identical to inspected base: canonical data-quality, canonical writer-coordination and historical evidence delta.
- [x] `openspec validate verify-delisting-metadata --strict --no-interactive`: `Change 'verify-delisting-metadata' is valid`.
- [x] `openspec validate data-quality --type spec --strict --no-interactive`: `Specification 'data-quality' is valid`. Existing canonical informational notices flag long requirements; no validation error.
- [x] `git diff --check`: passes.

## Artifact Paths

- Delta/support: `openspec/changes/verify-delisting-metadata/`
- Plan: `docs/superpowers/plans/2026-10-04-verified-delisting-metadata.md`
- Preflight/case ledger: `docs/superpowers/evidence/verified-delisting-metadata/preflight.md`
- Supplied proof (scratch, not committed): `/home/hermes/.hermes/cache/scratch/listed-till-proof-z711s6h3/results.json`

## Future Execution Gate — Not Performed Here

- [ ] Capture actual RED output and counts from planned CLI regressions before production edits.
- [ ] Implement narrow CLI change; capture GREEN, related and full backend ≥95% reports.
- [ ] Independent exact-SHA spec/quality review; approved PR/CI and operator/cron merge.
- [ ] Restricted WAL-aware backup, integrity and exact-SHA deployment/readback in authorized parent flow.
- [ ] Bounded offline fixture smoke, exact SQL preservation/idempotency and remaining standing-goal report.

Store future execution reports under a distinct scratch `verified-delisting-metadata` namespace and link exact paths/SHA/counts here after execution. This docs-only pass does not run tests, change source/test/config, read production/secrets, access live network, push, merge or deploy. It makes no seven-day autonomous acceptance claim. A temporary syntax-check wrapper lacked the loop for a caller fragment; correcting the checker context made all five blocks compile without changing source or tests.
