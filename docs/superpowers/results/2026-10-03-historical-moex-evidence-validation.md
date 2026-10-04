# Historical MOEX evidence — final validation, 2026-10-03

## Provenance and scope

Validated source HEAD: `e1ca79edec7310757e89d469679c47707665de85` on
`fix/historical-moex-evidence`. The controller ran and verified the final
isolated whole-backend gate; this docs-only bookkeeping step inspected its
saved output, JUnit XML, and raw coverage JSON. It did not rerun the gate
or claim a separate test execution.

Exact gate command:

```bash
/home/hermes/.hermes/cache/scratch/moex-full-suite-release/isolated-suite.sh gate
```

The test tree at `/home/hermes/.hermes/cache/scratch/moex-full-suite-release/repo`
is an archived tree, not a Git checkout. Byte comparison against the exact
HEAD found no differences in 244 tracked API source, test, script, and
`pyproject.toml` files. The isolated environment masks the Hermes directory,
uses scratch-owned HOME/data/temp paths and a read-only existing venv,
and exposes namespace-local loopback only, with no external network route.
No API tokens were used for this gate.

## Recorded result

Controller-verified gate: PASS. Saved pytest output reports:

```text
Required test coverage of 95.0% reached. Total coverage: 97.25%
1250 passed, 8 skipped, 1 xpassed, 47 warnings in 162.16s (0:02:42)
```

JUnit records 1259 tests, 0 failures, 0 errors, and 8 skipped, at
`2026-10-03T23:02:18.941549+03:00`. The one non-strict XPASS is
`tests/ingestion/test_real_client.py::test_real_client_init_raises_on_missing_sdk`:
its known import-cache condition means the SDK was already imported before
its `builtins.__import__` patch. It is not counted as an ordinary pass.

All eight skips say `sandbox tests disabled`; these are optional real-sandbox
integration tests gated by `RUN_SANDBOX_INTEGRATION`, not skipped backend
unit tests or evidence that live sandbox integration works:

- `tests/ingestion/test_backfill_sandbox.py::test_sandbox_backfill_one_ticker_sber`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_wrapper_propagates_auth_errors`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_get_accounts_returns_list`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_get_shares_returns_at_least_one_instrument`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_get_candles_returns_ohlcv_rows`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_token_in_db_is_used_by_real_client`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_aclose_cleans_up_async_client`
- `tests/ingestion/test_real_sandbox.py::test_sandbox_aclose_is_safe_when_never_opened`

## Coverage interpretation

Scope is the entire configured backend package `algotrader_api`, not only
the MOEX files, frontend, or every file in the repository. Raw totals:

- Combined statement/branch coverage: **97.25%**; 4512 covered executable
  lines plus 1121 covered branches out of 4622 lines plus 1170 branches.
- Executable-line coverage: **97.62%** (4512/4622); 110 missing lines.
- Branch coverage: **95.81%** (1121/1170); 49 missing branches,
  45 partial branches.
- Executed function regions: **98.54%** (406/412).

Function-region counting uses every entry in each file's coverage JSON
`functions` mapping with `summary.num_statements > 0`, and counts a region
as executed when `summary.covered_lines > 0`. This includes the unnamed
module-level region. A named-function-only count (excluding unnamed regions)
is **98.31%** (350/356). Both counts exclude zero-statement regions and do
not mean all statements in a function executed. These are descriptive
supplementary metrics, not an official function gate.

### SDK initialization-budget ruling

The prerequisite SDK fix uses one monotonic deadline across the synchronous
constructor and asynchronous `__aenter__`. A late result is rejected before
publishing client/services, and an owned partial client receives the existing
best-effort cooperative cleanup. A synchronous constructor is **not**
preemptible: a 2-second constructor with a 0.3-second budget is rejected only
after it returns. Cleanup has its separate cooperative 5-second timeout;
neither timeout is an absolute hard-kill bound. No threads/executors were
introduced, and no initialization secrets are logged.

`apps/api/pyproject.toml` has `source = ["algotrader_api"]`, `branch = true`,
and the existing combined `fail_under = 95`. It defines no separate
executable-line, branch, or function threshold. These four measurements
are not four independent repository-wide gates and do not establish
95% coverage for every individual module.

Baseline configured omissions remain `ingestion/real_client.py`,
`maintenance/*`, and `scripts_import/*`. Baseline `exclude_lines` remain
`pragma: no cover`, `if ev.type == "done":`, `raise NotImplementedError`,
`if tmp_path.exists`, and `target = None$`. The raw report contains 65
excluded lines. `pyproject.toml` is unchanged from implementation base
`3ad3977`; no new exclusion rules or threshold changes were used to pass.
Coverage therefore describes the configured denominator, not excluded code.

## Change acceptance and review basis

The controller supplied independent approvals for Tasks 1–3, final
spec/quality review, whole-branch F1/F2/F3 fixes, verification-test-wave,
and the final SDK cumulative-budget fix. The active plan's final-fix ruling
requires explicit ordered `from_d`/`to_d` windows and strict explicit-zero
shape validation. Older illustrative plan/spec examples are not a claim
that the legacy list-only fetcher now returns a tuple or that the canonical
coverage gate returns an `ok=True` object.

The reviewed real public-walker TEMP-DB test establishes PRE `2/2810`,
reason `incomplete`, POST `2/2`, 2808 explicit-zero evidence rows, and
`check_coverage(conn, [figi]) == []`. The real `populate_expected_bars`
runs before and after evidence on the TEMP DB. Exactly two real-shape bars
remain; no fabricated OHLC bars, manual denominator override, production
read, or live broker call is involved. The CLI partial-outcome re-review
establishes exit `0` and zero evidence without a new argument/exit branch.

The spec delta has one capability, `data-quality`, with two ADDED
Requirements and 11/14 GIVEN/WHEN/THEN scenarios; no MODIFIED or REMOVED
Requirements. Fresh docs validation command:

```bash
git diff --check && openspec validate persist-historical-moex-evidence --strict --no-interactive
```

Validator result: `Change 'persist-historical-moex-evidence' is valid`.

## Evidence locations and limits

Inspected artifacts under `/home/hermes/.hermes/cache/scratch/moex-full-suite-release/`:
`full-gate-output.txt`, `pytest.xml`, `coverage.json`, and `isolated-suite.sh`.
The results above are recorded here so acceptance does not depend only on
scratch-path survival.

This closes change acceptance, not full product readiness or autonomous
operation. The controller's existing real-production ML coverage observation
is **90.8407%**, separate from code-test coverage; this gate does not improve
that observation or prove production freshness/autonomy. It does not prove
recovery of all 215 incomplete-history figis or explain the daily phase hang.
No production data/service/config change, deployment, PR, or commit was made
by this documentation step. Unrelated `data/ui_snapshot.json` remains untouched.
