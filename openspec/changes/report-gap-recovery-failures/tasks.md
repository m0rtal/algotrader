# Tasks

## 1. Implementation: truthful trailing status

- [x] Parent completes independent preflight review of delta, design, plan, and plan-owned ledger before any source/test edit.
- [x] Add actual-import offline regression tests from `docs/superpowers/plans/2026-10-04-gap-recovery-failure-status.md` and record assertion RED against unchanged source.
- [x] Change only the scoped worker observer, unique failure aggregation, bounded diagnostics, and summary status; preserve historical/lock/lifetime policies.
- [x] Record targeted GREEN and unchanged regression behavior; commit exact source/tests. Independent source/task review and final whole-branch approval supplied by parent; approval is not represented as having preceded the first implementation commit.

## 2. Release verification (docs-only)

- [x] Record actual full backend regression, unchanged 95% coverage gate, and focused worker coverage for exact candidate `2586e66b86e3f46aec0151c565cc2f44ffdb076a`; consume completed isolated gate without rerunning unchanged source.
- [x] Record independent full-branch review: parent-supplied `deleg_f285050d`, `SPEC ✅ + Approved`, no blockers, for the implementation candidate. This is not CI or approval of a later documentation SHA.
- [ ] Parent verifies every required CI status on exact final candidate SHA, including branch-name-check and externally configured gates.
- [x] After reviewed GREEN apply the additive requirement to canonical data-quality and strict-validate change and canonical separately.
- [x] Write local release evidence with exact implementation commits, commands, counts, warnings, unresolved gates, and scope limits; commit only documentation/spec paths. See `docs/superpowers/results/2026-10-04-gap-recovery-failure-status.md` and ignored Task 2 report for resulting docs SHA.
- [ ] Parent verifies publication/push and required gates on exact documentation head before merge, rollout, or archive.
- [ ] Parent records actual approved rollout/deployment proof; archive only after verified publication and approved completion.
