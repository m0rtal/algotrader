# SQLite evidence BUSY deferral implementation plan

Scope approved by the delegated task: listed-till and record_no_trade_evidence only. Preserve canonical writer-coordination ownership, separate transactions, bounded diagnostics, and borrowed connections.

1. Strictly validate sqlite-evidence-busy-defer before implementation.
2. RED: file-backed independent BEGIN IMMEDIATE blocker; real flock; short test-only busy_timeout. Exercise actual CLI main with offline matching metadata and zero-trade fixtures. Add commit-failure and non-BUSY rollback checks, checking transaction state at unlock and connection usability afterward.
3. GREEN: share numeric primary-code classification, catch transaction and commit failures inside flock, rollback then narrowly translate BUSY with cause.
4. Run scoped CLI/evidence and writer-lock regressions using the existing API venv with PYTHONPATH/PYTHONHOME unset. Do not run production smokes or the broad host-sensitive suite.
5. Commit exact docs, source and test paths. Write task report with RED/GREEN counts and commit SHA. Independent review, safe full gate, PR publication, merge and deployment belong to the parent.
