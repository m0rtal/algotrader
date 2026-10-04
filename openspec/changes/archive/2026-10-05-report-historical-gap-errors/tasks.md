# Tasks

## 1. Worker-only implementation

- [x] 1.1 Verify parent RED/control against exact baseline; add bounded real-path regressions and failure attribution controls.
- [x] 1.2 Extend only the worker-local error observer and distinct failure aggregation; keep helper/runner interfaces and policy unchanged.
- [x] 1.3 Verify GREEN targeted/regression suites, exact candidate isolated full backend coverage gate and independent spec/quality review.

## 2. Parent-owned release

- [x] 2.1 Apply only the approved additive canonical requirement and record real evidence after Task 1 review/gates.
- [x] 2.2 Publish PR #191, verify its exact head/check, merge and perform the backup-first exact-SHA production fast-forward with the two scoped daily-worker child reloads.
- [x] 2.3 Run the network-disabled bounded deployed-code fixture smoke, read back backup integrity/health/unchanged cron and measure the canonical coverage gate read-only; retain unfulfilled freshness and seven-day acceptance explicitly.
