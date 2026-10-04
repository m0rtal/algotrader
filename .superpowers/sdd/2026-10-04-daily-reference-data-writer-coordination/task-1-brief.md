### Task 1: Exact diagnostic identities and honest inventory transition

**Files:** Modify `ingestion/writer_lock.py:39-71`, `tests/test_writer_lock.py`, `tests/test_writer_lock_diagnostics.py`, `tests/test_writer_inventory.py`.

**Interfaces:** Consumes unchanged `writer_lock(db_path, *, role, phase, timeout_seconds=30.0)`, `WriterLockReentrant`, `WriterRole`, `WriterPhase`, `VALID_ROLES`, `VALID_PHASES`. Produces additional valid role/phase literals only; no new lock primitive.

- [ ] **Step 1: Add role/phase RED tests.** Append to existing lock tests:

```python
from typing import get_args
from algotrader_api.ingestion import writer_lock as lock_module

NEW_PAIRS = [
    ("universe-sync", "instruments"),
    ("backfill-metadata", "instruments"),
    ("backfill-metadata", "metadata"),
    ("corporate-actions", "corporate-actions"),
    ("corporate-actions", "adjusted-bars"),
    ("dividends", "dividends"),
]

@pytest.mark.parametrize("role,phase", NEW_PAIRS)
def test_daily_role_phase_is_valid(tmp_path, role, phase):
    assert role in get_args(lock_module.WriterRole)
    assert role in lock_module.VALID_ROLES
    assert phase in get_args(lock_module.WriterPhase)
    assert phase in lock_module.VALID_PHASES
    with lock_module.writer_lock(tmp_path / "state.db", role=role,
                                 phase=phase, timeout_seconds=0.05):
        pass
```

Run `test_writer_lock.py -k daily_role_phase -q`; expected RED: new literal absent. Extend both Literals and both frozensets with the exact values above. Rerun GREEN and all existing diagnostics tests; old valid and invalid strings retain their behavior.

- [ ] **Step 2: Pin existing thread guard, without changing its semantics.** Add a contender-thread test in the existing lock test file. It must collect exactly one `WriterLockReentrant`, never execute the contender body, and join the thread while the outer owner remains held:

```python
def test_daily_role_does_not_bypass_same_process_thread_guard(tmp_path):
    db = tmp_path / "thread.db"
    outcomes = []

    def contender():
        try:
            with lock_module.writer_lock(db, role="dividends",
                                         phase="dividends", timeout_seconds=0.05):
                outcomes.append("entered")
        except lock_module.WriterLockReentrant:
            outcomes.append("rejected")

    with lock_module.writer_lock(db, role="universe-sync", phase="instruments"):
        thread = threading.Thread(target=contender)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert outcomes == ["rejected"]
    with lock_module.writer_lock(db, role="dividends", phase="dividends"):
        pass
```

This is an existing-invariant regression; do not pretend it needs a primitive fix or change it into same-process queuing.

- [ ] **Step 3: Preserve original inventory and add an explicit parallel owner inventory.** Keep `IN_SCOPE_FUNCTIONS` and its special script markers. Define this exact new mapping, with paths relative to existing `_SRC_ROOT` and `_REPO_ROOT`:

```python
DAILY_TRANSACTION_OWNERS = {
    "universe-sync/instruments:upsert_instruments": _INGEST / "universe.py",
    "backfill-metadata/instruments:BackfillRunner._upsert_instrument": _INGEST / "backfill.py",
    "backfill-metadata/metadata:BackfillRunner._seed_metadata_for_figi": _INGEST / "backfill.py",
    "backfill-metadata/metadata:BackfillRunner._upsert_metadata": _INGEST / "backfill.py",
    "corporate-actions/corporate-actions:merge_into_corporate_actions": _SRC_ROOT / "scripts_import" / "import_corporate_actions_common.py",
    "corporate-actions/adjusted-bars:_step_corporate_actions": _REPO_ROOT / "apps" / "api" / "worker.py",
    "dividends/dividends:merge_into_dividends": _SRC_ROOT / "scripts_import" / "import_corporate_actions_common.py",
}
SUPPLEMENTAL_BAR_OWNERS = {
    "bar-writer/bars:replace_bars_for_figi_with_rowcount": _SRC_ROOT / "db" / "bars_sqlite.py",
}

def _qualified_function(tree, qualified_name):
    parts = qualified_name.split(".")
    body = tree.body
    for class_name in parts[:-1]:
        classes = [n for n in body if isinstance(n, ast.ClassDef)
                   and n.name == class_name]
        assert len(classes) == 1, qualified_name
        body = classes[0].body
    functions = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == parts[-1]]
    assert len(functions) == 1, qualified_name
    return functions[0]

@pytest.mark.parametrize("identity,path", list(DAILY_TRANSACTION_OWNERS.items()),
                         ids=list(DAILY_TRANSACTION_OWNERS))
def test_daily_owner_symbol_present(identity, path):
    assert path.exists()
    _qualified_function(_parse(path), identity.split(":", 1)[1])
```

The qualified resolver must not compare prefixed inventory keys directly to `ast.FunctionDef.name`; that would give false missing-symbol failures. Supplemental bar owner uses the same resolver.

- [ ] **Step 4: Replace only approved negatives; retain stronger proofs.** Remove exactly universe and dividend-fetcher paths from whole-module `OUT_OF_SCOPE_MODULES`. Retain the six other old entries; add `db/sqlite.py` and `db/migrations_runner.py`. Remove silent `if p.exists()` filtering and assert that each path exists. Add explicit no-direct-acquisition function inventory from design, including discovery, fetch `_run`, queue/dequeue, heartbeat/log/pipeline helpers, breaker writes and non-owning forward-adjustment helpers. Catching/importing exception/formatter is allowed in adapters; acquiring is not.

Scan source AST calls, including `writer_lock(...)` and module/alias-qualified acquisition calls. For each production module resolve lock import aliases once and reject acquisition calls/decorators in excluded bodies. For borrowers additionally reject new `commit`, `rollback` or `close` calls. Build the process-creation scan from the deduplicated union of original, supplemental and daily owner files. Keep the existing raw-bar and marker tests unchanged. A transitive call to an approved owner is allowed; wrapping its whole caller is not.

Do not temporarily xfail or delete the new positive boundary tests. Introduce each owner's runtime assertion in its own task so each commit is executable and honest.

- [ ] **Step 5: Run primitive/inventory tests, check diff and commit.**

```bash
env -u PYTHONPATH -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py -q -p no:cacheprovider
git diff --check
git add apps/api/src/algotrader_api/ingestion/writer_lock.py \
  apps/api/tests/test_writer_lock.py apps/api/tests/test_writer_lock_diagnostics.py \
  apps/api/tests/test_writer_inventory.py
git commit -m "test(ingestion): define daily writer scope and identities"
```

Independent review: exact seven-owner mapping; all old values/invariants retained; exclusions explicit; no global helper lock.

---

## R1: path-aware relative-import inventory resolution

Review base: `2211cb8ca468252783ab4ce418df47f0f23c1640`. The P2 finding is that
`from .. import ingestion as ingest` has `ImportFrom.module is None`; the old
resolver constructed `None.ingestion` and missed acquisition by the alias.

R1 is limited to `apps/api/tests/test_writer_inventory.py` and appended Task 1
SDD evidence. Production identities, owner implementations and later tasks are
unchanged. Keep the existing matcher; do not add a general AST interpreter or
import production modules to resolve names.

- [x] Reproduce the bypass before the fix: forbidden `discover_universe` calls
  and locking decorators at relative levels 1 and 2 must both be rejected.
- [x] Pass the actual source path through inventory and scanner call sites.
  Derive the package from the source's real `__init__.py` parents, including
  package initializer files; resolve `ImportFrom.level` and nullable `module`
  with stdlib `importlib.util.resolve_name`.
- [x] Reject missing package metadata, standalone relative imports and imports
  beyond the top-level package. Do not assume every tree is `algotrader_api`.
- [x] Retain bare acquisitions, import aliases, qualified module calls,
  exception/formatter adapters and returned-context wrapper scanning.
- [x] Verify RED, all inventory tests and the existing scoped ownership/thread
  guard regressions. Preserve every one of the 198 baseline test cases.
- [x] Append reproducible commands, counts and blob/script SHAs to the existing
  `task-1-report.md`; commit only exact Task 1 test/evidence paths locally.

R1 implementation and scoped checks are complete. The parent must independently
re-review the exact commit returned by this leaf before starting Task 2. This
revision does not claim full operational, coverage or scheduled-cycle acceptance.
