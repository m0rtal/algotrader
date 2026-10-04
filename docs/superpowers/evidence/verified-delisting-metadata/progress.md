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

## Docs Preflight Amendment — `verified-delisting-doc-preflight`

Scope: the three independent preflight findings only, starting at `7063f0526b884b42bb2b805b7dabf7bc276819a8`. No implementation or source/test file was written. The supplied 13-case ledger and its provenance remain unchanged.

- [x] P1 board identity: require the exact non-empty `secid` column/value in every board row to match the requested ticker, independently of description SECID. Document real-CLI preservation cases with missing/empty/mismatched SECID and a wrong second row, both non-empty and `lo > win_hi` windows, and prior NULL/non-NULL state.
- [x] P2 partial fixture: remove `_fetch_year_moex_outcome` monkeypatch. One valid history row and cursor columns `INDEX`, `TOTAL`, `PAGESIZE`, data `[[0, 2, 2]]` exercise the actual parser. The bounded documentation check returned `partial`, one retained row and exactly one transport call.
- [x] P2 activity forms: synchronize delta/design/reference/tests. Accept inactive exact integer `0`, boolean `False`, exact string `"0"`; reject active exact integer `1`, boolean `True`, exact string `"1"` and every other invalid form without truthiness or integer coercion.
- [x] Clarify latest specific user/operator full-rollout authorization: parent owns merge after independent reviews and CI on the exact SHA. Stale cron deferral does not override this authorization; implementer self-merge and bypassing CI/production safeguards remain prohibited. No merge is performed here.
- [x] Five Python fenced blocks compile; bounded reference probes accept all three inactive forms and reject all 13 tested active/invalid forms. Correct description `GAZP` with boards `SBER` was accepted by the prior integer-0 reference but is rejected by the amended reference with either integer `0` or boolean `False`. Matched boolean `False` remains accepted. Missing/empty/null/padded/other-row SECID is rejected; latest valid board selection is retained.
- [x] Strict delta and canonical data-quality validations pass. Three authority files remain byte-identical to `ad58b28`; no canonical apply/archive or production denominator cleanup. `git diff --check` passes.

Exact commands, from the worktree root:

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 /home/hermes/algotrader/apps/api/.venv/bin/python /home/hermes/.hermes/cache/scratch/verified-delisting-doc-preflight/check_docs.py
openspec validate verify-delisting-metadata --strict --no-interactive
openspec validate data-quality --type spec --strict --no-interactive
git diff --check
```

Validator/probe artifact: `/home/hermes/.hermes/cache/scratch/verified-delisting-doc-preflight/check_docs.py`; fresh combined output: `/home/hermes/.hermes/cache/scratch/verified-delisting-doc-preflight/validation.txt`. The parser check executes the actual source function bodies in an isolated namespace with transport-only fakes, without importing production configuration. This is documentation validation, not actual CLI RED/GREEN or a backend-suite pass. OpenSpec emitted only existing informational long-requirement notices.

Existing full review package: `/home/hermes/worktrees/algotrader-verified-delisting/.superpowers/sdd/2026-10-04-verified-delisting-metadata/review-ad58b28..7063f05.diff`; brief: `task-1-brief.md` in that same directory. Parent regenerates the package after this documentation commit; this leaf does not substitute its own review artifact.

## Docs Preflight Amendment — Round 2 Active/Inactive Path Separation

Scope: remaining P2 path blocker only, starting at `54d41a52a7bff098f8f19630b5b72d339451da73`. Modified only this progress report, plan, design and data-quality delta. No source/test/config, production, network, secrets, push, merge or deployment access.

- [x] Reproduced the incorrect primary rejection assumption with the actual extracted `_get_meta_moex`: TQBR `1`, `True` and `1.0` return active metadata; string `"1"` returns None. The shared parser remains unchanged.
- [x] Put all 13 strict inactive-probe rejection forms on non-primary `OTHER`, alone or alongside inactive TQBR. All 26 payloads return actual active metadata None and strict reference None. Strict inactive acceptance remains only `0`, `False`, `"0"`; both floats remain rejected by the inactive probe.
- [x] Added separate active-primary controls for the three accepted forms, both bar dates and prior NULL/non-NULL listed-till. They require ordinary partial history and no delisting UPDATE, not fictional no-history rejection.
- [x] Corrected the documented first-history observer to declare expected mutation: false for active controls, true for validated inactive metadata. SQL readback and a CLI-local connection trace check UPDATE before the observer records history. No shared SQLite or requests binding is mutated.
- [x] Ran the documented observer and actual extracted metadata/year-parser functions through transport-only fakes and scratch SQLite: 12 active history checks preserve instrument state with no UPDATE; 6 inactive history checks observe committed mutation/UPDATE; 6 valid inactive empty windows request no history. This is a reference-ordering check, not actual CLI RED/GREEN or a migrated fixture acceptance run. No parser outcome was faked.
- [x] Five Python fenced blocks compile. Retained real year-parser partial check returns `partial`, one row and one HTTP call. Prior board SECID and strict metadata checks still pass. Three canonical/retained authority files remain byte-identical to `ad58b28`.
- [x] Strict change and canonical data-quality validations exit 0; existing informational long-requirement notices only. `git diff --check` exits 0.
- [x] Documented ruling cost in design ruling 6: preserving existing active-parser semantics may leave an active-path metadata weakness if this scope choice is wrong; investigate that in a separate audit, not by silently widening this delisting fix.

Exact offline checker command (exit 0):

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 /home/hermes/algotrader/apps/api/.venv/bin/python /home/hermes/.hermes/cache/scratch/verified-delisting-doc-preflight-round2/check_paths.py
openspec validate verify-delisting-metadata --strict --no-interactive
openspec validate data-quality --type spec --strict --no-interactive
git diff --check
```

Checker output: `/home/hermes/.hermes/cache/scratch/verified-delisting-doc-preflight-round2/validation.txt`. The round-2 checker reuses the prior scoped checker for syntax, strict-reference, board identity, partial parser and authority checks, then verifies actual active routing and documented observer behavior. Parent regenerates the brief/diff and obtains scoped independent re-review after this commit. Future implementation/release gates remain unchecked.

## Future Execution Gate — Not Performed Here

- [ ] Capture actual RED output and counts from planned CLI regressions before production edits.
- [ ] Implement narrow CLI change; capture GREEN, related and full backend ≥95% reports.
- [ ] Independent exact-SHA spec/quality review; approved PR/CI and parent-owned merge under the latest specific user/operator authorization.
- [ ] Restricted WAL-aware backup, integrity and exact-SHA deployment/readback in authorized parent flow.
- [ ] Bounded offline fixture smoke, exact SQL preservation/idempotency and remaining standing-goal report.

Store future execution reports under a distinct scratch `verified-delisting-metadata` namespace and link exact paths/SHA/counts here after execution. This docs-only pass does not run tests, change source/test/config, read production/secrets, access live network, push, merge or deploy. It makes no seven-day autonomous acceptance claim. A temporary syntax-check wrapper lacked the loop for a caller fragment; correcting the checker context made all five blocks compile without changing source or tests.
