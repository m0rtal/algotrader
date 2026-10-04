# Parent verified preflight

Base/deployed source: `2298a11413fd0eac12cf82b405b6ec09b5193389`. Parent compared actual worker, runner and helper source byte-for-byte against the masked archived source.

Actual reproduction: `/home/hermes/.hermes/cache/scratch/delisting-release-parent/historical-probe.log` and `historical-probe.xml`; executable in that directory's `repo/apps/api/tests/test_parent_historical_gap_probe.py`. Two tests: one expected RED at the desired false-phase assertion, one healthy-empty PASS, no errors/skips, inner exit1. Faulted transport: one call, actual ticker_progress/error, persisted error metadata, phase=True/failed=0; healthy empty emits empty/skipped and phase=True. Both fixture DBs integrity ok, exactly two unchanged seed bars, client closed. No production mutation.

All new implementation GREEN/full/independent-review/PR/CI/merge/deploy gates are pending. This record does not reproduce a production fetch, prove upstream availability, or certify freshness, cohort coverage or seven-day autonomy. A prior leaf's nonexistent artifact claim was rejected and is not evidence.
