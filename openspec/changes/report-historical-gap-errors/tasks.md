# Tasks

## 1. Worker-only implementation

- [ ] 1.1 Verify parent RED/control against exact baseline; add bounded real-path regressions and failure attribution controls.
- [ ] 1.2 Extend only the worker-local error observer and distinct failure aggregation; keep helper/runner interfaces and policy unchanged.
- [ ] 1.3 Verify GREEN targeted/regression suites, exact candidate isolated full backend coverage gate and independent spec/quality review.

## 2. Parent-owned release

- [ ] 2.1 Apply only the approved additive canonical requirement and record real evidence after Task 1 review/gates.
- [ ] 2.2 Publish PR, verify exact head/checks, parent-authorized merge and backup-first exact-SHA rollout with scoped affected worker reload.
- [ ] 2.3 Run bounded deployed-code fixture smoke, read back DB integrity/health/unchanged cron and canonical readiness; retain unmet freshness and seven-day gates.
