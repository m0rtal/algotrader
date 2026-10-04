"""Static inventory tests for the writer-coordination capability.

Asserts that:

* The approved in-scope market-data mutation paths exist and remain
  represented after the coordinated refactor.
* `_async_backfill_impl` no longer contains a raw ``INSERT INTO bars``
  literal in executable code (only comments/docstrings may mention
  it).
* No ``with writer_lock(...)`` body contains ``os.fork``,
  ``subprocess``, or ``multiprocessing`` calls (no process creation
  inside the critical section).
* Out-of-scope modules do not import or call ``writer_lock``.
* Daily owner symbols are explicit; their runtime protection is tested
  when each owner is implemented, not inferred from this inventory.
* Non-owning orchestration and adjustment borrowers never acquire; the
  borrowers do not commit, roll back, or close the caller's connection.
"""
from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]  # apps/api/tests/x.py → repo root
_SRC_ROOT = _REPO_ROOT / "apps" / "api" / "src" / "algotrader_api"
_INGEST = _SRC_ROOT / "ingestion"

IN_SCOPE_FUNCTIONS = {
    "replace_bars_for_figi": _SRC_ROOT / "db" / "bars_sqlite.py",
    "_async_backfill_impl": _INGEST / "backfill.py",
    "record_no_trade_evidence": _INGEST / "no_trade_evidence.py",
    "reconcile_no_trade_evidence": _INGEST / "no_trade_evidence.py",
    "expected_bars": _REPO_ROOT / "apps" / "api" / "scripts" / "populate_expected_bars.py",
    "cron_expected_bars.sh": _REPO_ROOT / "apps" / "api" / "scripts" / "cron_expected_bars.sh",
}

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
OWNER_MODULES = set(IN_SCOPE_FUNCTIONS.values()) | set(DAILY_TRANSACTION_OWNERS.values()) | set(SUPPLEMENTAL_BAR_OWNERS.values())

OUT_OF_SCOPE_MODULES = [
    _SRC_ROOT / "pipeline" / "assertions.py",
    _SRC_ROOT / "data_quality" / "service.py",
    _SRC_ROOT / "data_quality" / "completeness.py",
    _SRC_ROOT / "maintenance" / "cleanup.py",
    _INGEST / "universe_sync.py",
    _SRC_ROOT / "scripts_import" / "import_corporate_actions.py",
    _SRC_ROOT / "db" / "sqlite.py",
    _SRC_ROOT / "db" / "migrations_runner.py",
]


NON_OWNING_FUNCTIONS = {
    "discover_universe": _INGEST / "universe.py",
    "fetch_and_persist": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "fetch_and_persist._run": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "_list_tradeable_figis": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "_read_pending_figis": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "_queue_throttled_figi": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "_dequeue_figi": _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
    "heartbeat_loop": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_heartbeat_loop": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_log_chain_phase": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_write_pipeline_run": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_step_guardian": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_step_freshness_check": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_step_universe_sync": _REPO_ROOT / "apps" / "api" / "worker.py",
    "_step_dividends": _REPO_ROOT / "apps" / "api" / "worker.py",
    "run_daily_chain": _REPO_ROOT / "apps" / "api" / "worker.py",
    "run_live_mode": _REPO_ROOT / "apps" / "api" / "worker.py",
    "BackfillRunner._log": _INGEST / "backfill.py",
    "BackfillRunner._emit": _INGEST / "backfill.py",
    "BackfillRunner._discover_universe": _INGEST / "backfill.py",
    "_tinkoff_breaker_record_failure": _INGEST / "backfill.py",
    "_tinkoff_breaker_record_success": _INGEST / "backfill.py",
}
BORROWED_ADJUSTMENT_FUNCTIONS = {
    "apply_all_pending": _SRC_ROOT / "data_quality" / "forward_adjustment.py",
    "apply_forward_split": _SRC_ROOT / "data_quality" / "forward_adjustment.py",
    "_already_applied": _SRC_ROOT / "data_quality" / "forward_adjustment.py",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text())


def _functions_in_tree(tree: ast.Module) -> set[str]:
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _async_functions_in_tree(tree: ast.Module) -> set[str]:
    return {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
    }


def _qualified_function(tree: ast.Module, qualified_name: str):
    parts = qualified_name.split(".")
    body = tree.body
    # Descend only through the named class/function, including nested _run.
    for scope_name in parts[:-1]:
        scopes = [n for n in body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.name == scope_name]
        assert len(scopes) == 1, qualified_name
        body = scopes[0].body
    functions = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == parts[-1]]
    assert len(functions) == 1, qualified_name
    return functions[0]


@pytest.fixture(scope="module")
def source_inventory():
    paths = (OWNER_MODULES | set(OUT_OF_SCOPE_MODULES)
             | set(NON_OWNING_FUNCTIONS.values()) | set(BORROWED_ADJUSTMENT_FUNCTIONS.values()))
    sources = {}
    for path in sorted(paths):
        assert path.exists(), f"missing inventory file {path}"
        if path.suffix == ".py":
            tree = _parse(path)
            sources[path] = tree, _lock_acquisition_names(tree, path)
    return sources


# ---------------------------------------------------------------------------
# In-scope paths remain represented
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,path",
    list(IN_SCOPE_FUNCTIONS.items()),
    ids=list(IN_SCOPE_FUNCTIONS.keys()),
)
def test_in_scope_symbol_present(name, path):
    """The approved in-scope mutation entry points still exist after
    each task's edits.
    """
    assert path.exists(), f"missing file {path}"
    if name.endswith(".sh"):
        text = path.read_text()
        assert "populate_expected_bars" in text
        return
    # `expected_bars` is a column referenced by a procedural script —
    # accept string presence rather than a function name.
    if name == "expected_bars":
        assert "expected_bars" in path.read_text()
        return
    tree = _parse(path)
    if name.startswith("_async"):
        assert name in _async_functions_in_tree(tree), (
            f"{name} missing from {path}"
        )
    else:
        assert name in _functions_in_tree(tree), (
            f"{name} missing from {path}"
        )


def test_listed_till_present_in_backfill():
    """`listed_till` appears in `backfill.py` — the in-scope marker is
    the UPDATE/INSERT that sets the column on the instruments table.
    """
    text = IN_SCOPE_FUNCTIONS["_async_backfill_impl"].read_text()
    assert "listed_till" in text


def test_expected_bars_present_in_populate_script():
    text = IN_SCOPE_FUNCTIONS["expected_bars"].read_text()
    assert "expected_bars" in text


@pytest.mark.parametrize("identity,path", list(DAILY_TRANSACTION_OWNERS.items()),
                         ids=list(DAILY_TRANSACTION_OWNERS))
def test_daily_owner_symbol_present(identity, path, source_inventory):
    _qualified_function(source_inventory[path][0], identity.split(":", 1)[1])


@pytest.mark.parametrize("identity,path", list(SUPPLEMENTAL_BAR_OWNERS.items()),
                         ids=list(SUPPLEMENTAL_BAR_OWNERS))
def test_supplemental_bar_owner_symbol_present(identity, path, source_inventory):
    _qualified_function(source_inventory[path][0], identity.split(":", 1)[1])


def test_qualified_function_does_not_match_another_class():
    tree = ast.parse(
        "class Other:\n    def _upsert_metadata(self): pass\n"
        "class BackfillRunner:\n    pass\n"
    )
    with pytest.raises(AssertionError, match="BackfillRunner._upsert_metadata"):
        _qualified_function(tree, "BackfillRunner._upsert_metadata")


# ---------------------------------------------------------------------------
# Raw INSERT INTO bars must be removed from _async_backfill_impl
# ---------------------------------------------------------------------------


def _string_constants(node: ast.AST) -> list[str]:
    """Walk an AST collecting every string constant in executable
    positions. Comments and docstrings are NOT filtered here — that
    is the caller's job (because filtering requires knowing whether
    the node is a stmt-of-docstring).
    """
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _func_body_strings(tree: ast.Module, func_name: str) -> list[str]:
    """Return string constants found inside the body of `func_name`,
    excluding the docstring (first Expr -> Constant right after the
    header).
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != func_name:
                continue
            body = node.body
            # Skip leading docstring.
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            out: list[str] = []
            for sub in body:
                out.extend(_string_constants(sub))
            return out
    return []


def test_async_backfill_impl_has_no_raw_insert_into_bars():
    tree = _parse(IN_SCOPE_FUNCTIONS["_async_backfill_impl"])
    strings = _func_body_strings(tree, "_async_backfill_impl")
    # Compare case-insensitively on both sides so a mixed-case literal
    # like "INSERT INTO bars" (uppercase verb, lowercase target) is
    # still detected.
    needle = "insert into bars"
    raw = [s for s in strings if needle in s.lower()]
    assert not raw, (
        f"_async_backfill_impl still contains raw INSERT INTO bars: {raw!r}"
    )


# ---------------------------------------------------------------------------
# No process creation inside writer_lock bodies
# ---------------------------------------------------------------------------


def _lock_acquisition_names(tree: ast.AST, source_path: Path | None = None) -> set[str]:
    names = {"writer_lock"}
    module = "algotrader_api.ingestion.writer_lock"
    package_parts = []
    if source_path is not None:
        # __init__.py belongs to its own directory's package, just like a module.
        for parent in source_path.resolve().parents:
            if not (parent / "__init__.py").is_file():
                break
            package_parts.append(parent.name)
    package = ".".join(reversed(package_parts))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_module = resolve_name("." * node.level + (node.module or ""), package)
            for alias in node.names:
                bound = alias.asname or alias.name
                if imported_module.split(".")[-1] == "writer_lock":
                    if alias.name == "writer_lock":
                        names.add(bound)
                elif alias.name == "writer_lock":
                    names.add(f"{bound}.writer_lock")
                else:
                    imported = f"{imported_module}.{alias.name}"
                    if module.startswith(imported + "."):
                        names.add(f"{bound}{module[len(imported):]}.writer_lock")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if module == alias.name or module.startswith(alias.name + "."):
                    names.add(f"{alias.asname or alias.name}{module[len(alias.name):]}.writer_lock")
    # Existing evidence ownership returns a context; entry happens at its caller.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(n, ast.Return) and isinstance(n.value, ast.Call)
                   and ast.unparse(n.value.func) in names for n in node.body):
                names.add(node.name)
    return names


def _writer_lock_calls(
    tree: ast.AST, acquisition_names=None, *, source_path: Path | None = None,
) -> list[ast.Call]:
    names = (_lock_acquisition_names(tree, source_path)
             if acquisition_names is None else acquisition_names)
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and ast.unparse(n.func) in names
    ]


def _acquisition_sites(node: ast.AST, names: set[str]) -> list[ast.AST]:
    calls = _writer_lock_calls(node, names)
    bare_decorators = [
        decorator
        for scope in ast.walk(node)
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        for decorator in scope.decorator_list
        if ast.unparse(decorator) in names
    ]
    return [*calls, *bare_decorators]


@pytest.mark.parametrize("import_statement,acquisition", [
    ("from algotrader_api.ingestion.writer_lock import writer_lock as acquire", "acquire"),
    ("from algotrader_api.ingestion import writer_lock as locks", "locks.writer_lock"),
    ("from .writer_lock import writer_lock as acquire", "acquire"),
    ("from . import writer_lock as locks", "locks.writer_lock"),
    ("import algotrader_api.ingestion.writer_lock as locks", "locks.writer_lock"),
    ("import algotrader_api.ingestion.writer_lock", "algotrader_api.ingestion.writer_lock.writer_lock"),
    ("from algotrader_api import ingestion as ingest", "ingest.writer_lock.writer_lock"),
    ("import algotrader_api as api", "api.ingestion.writer_lock.writer_lock"),
])
def test_writer_lock_scan_resolves_import_aliases(import_statement, acquisition):
    tree = ast.parse(
        f"{import_statement}\n"
        f"with {acquisition}(db, role='dividends', phase='dividends'):\n"
        "    pass\n"
    )
    assert len(_writer_lock_calls(tree, source_path=_INGEST / "universe.py")) == 1


def test_writer_lock_scan_allows_exception_and_formatter_adapters():
    tree = ast.parse(
        "from algotrader_api.ingestion.writer_lock import WriterLockBusy, format_busy_defer\n"
        "format_busy_defer(error)\n"
    )
    assert _writer_lock_calls(tree, source_path=_INGEST / "no_trade_evidence.py") == []


@pytest.mark.parametrize("decorator", ["acquire", "acquire(db)", "locks.writer_lock"])
def test_non_owner_scan_detects_lock_decorators(decorator):
    tree = ast.parse(
        "from algotrader_api.ingestion.writer_lock import writer_lock as acquire\n"
        "from algotrader_api.ingestion import writer_lock as locks\n"
        f"@{decorator}\ndef fetch(): pass\n"
    )
    path = _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py"
    assert len(_acquisition_sites(_qualified_function(tree, "fetch"),
                                  _lock_acquisition_names(tree, path))) == 1


@pytest.mark.parametrize("path,import_statement,acquisition", [
    pytest.param(_SRC_ROOT / "__init__.py", "from . import ingestion as ingest",
                 "ingest.writer_lock.writer_lock", id="level-1-package-alias"),
    pytest.param(_INGEST / "universe.py", "from .. import ingestion as ingest",
                 "ingest.writer_lock.writer_lock", id="level-2-package-alias"),
    pytest.param(_INGEST / "universe.py", "from . import writer_lock as locks",
                 "locks.writer_lock", id="level-1-lock-module-alias"),
    pytest.param(_SRC_ROOT / "data_quality" / "service.py",
                 "from ..ingestion import writer_lock as locks",
                 "locks.writer_lock", id="level-2-qualified-module-alias"),
])
@pytest.mark.parametrize("site", ["call", "decorator"])
def test_non_owner_scan_rejects_relative_import_acquisitions(
    source_inventory, path, import_statement, acquisition, site,
):
    """An imported package alias must not hide discovery's lock ownership."""
    assert path.is_file()
    lock_call = f"{acquisition}(db, role='dividends', phase='dividends')"
    if site == "call":
        source = f"def discover_universe(db):\n    with {lock_call}:\n        pass\n"
    else:
        source = f"@{lock_call}\ndef discover_universe(db):\n    pass\n"
    tree = ast.parse(f"{import_statement}\n{source}", filename=str(path))
    sources = {**source_inventory, path: (tree, _lock_acquisition_names(tree, path))}
    with pytest.raises(AssertionError, match="discover_universe acquires writer lock"):
        test_non_owner_does_not_acquire_writer_lock("discover_universe", path, sources)


@pytest.mark.parametrize("path,import_statement", [
    pytest.param(_SRC_ROOT / "__init__.py", "from .. import ingestion as ingest",
                 id="root-package-beyond-top-level"),
    pytest.param(_INGEST / "__init__.py", "from ... import ingestion as ingest",
                 id="nested-package-beyond-top-level"),
    pytest.param(_REPO_ROOT / "apps" / "api" / "worker.py",
                 "from . import ingestion as ingest", id="standalone-module"),
])
def test_writer_lock_scan_rejects_invalid_relative_imports(path, import_statement):
    tree = ast.parse(import_statement, filename=str(path))
    with pytest.raises(ImportError):
        _lock_acquisition_names(tree, path)


def test_writer_lock_scan_requires_relative_import_source_path():
    tree = ast.parse("from .. import ingestion as ingest")
    with pytest.raises(ImportError):
        _lock_acquisition_names(tree)


def test_writer_lock_scan_does_not_assume_canonical_package(tmp_path):
    package = tmp_path / "src" / "other_api"
    package.mkdir(parents=True)
    path = package / "__init__.py"
    path.write_text("")
    tree = ast.parse(
        "from . import ingestion as ingest\n"
        "def discover_universe(db):\n"
        "    with ingest.writer_lock.writer_lock(db, role='dividends', phase='dividends'):\n"
        "        pass\n",
        filename=str(path),
    )
    assert _writer_lock_calls(tree, source_path=path) == []


def _enclosing_with_for_call(tree: ast.Module, target: ast.AST):
    """Find the `with` statement that acquires `target`, not an outer body."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if any(sub is target for sub in ast.walk(item.context_expr)):
                    return node
    return None


def test_no_process_creation_inside_writer_lock_body(source_inventory):
    """No `os.fork`, `subprocess`, or `multiprocessing` calls inside
    any `with writer_lock(...)` body.
    """
    forbidden_names = {"fork", "Popen", "run", "spawn", "Process"}
    forbidden_modules = {"os", "subprocess", "multiprocessing"}

    for path in sorted(OWNER_MODULES):
        if path.suffix != ".py":
            continue
        tree, names = source_inventory[path]
        for call in _writer_lock_calls(tree, names):
            ctx = _enclosing_with_for_call(tree, call)
            if ctx is None:
                assert any(isinstance(n, ast.Return) and n.value is call for n in ast.walk(tree)), (
                    f"{path}:{call.lineno}: writer lock context must be used by `with` or returned"
                )
                continue
            for inner in ast.walk(ctx):
                if not isinstance(inner, ast.Call):
                    continue
                if isinstance(inner.func, ast.Name):
                    if inner.func.id in {"fork", "Popen", "run", "Process"}:
                        pytest.fail(
                            f"{path}: forbidden call {inner.func.id}() "
                            f"inside writer_lock body"
                        )
                elif isinstance(inner.func, ast.Attribute):
                    if inner.func.attr in forbidden_names:
                        # Only flag when the receiver resolves to a
                        # forbidden module name.
                        value = inner.func.value
                        if (
                            isinstance(value, ast.Name)
                            and value.id in forbidden_modules
                        ):
                            pytest.fail(
                                f"{path}: forbidden {value.id}."
                                f"{inner.func.attr}() inside "
                                f"writer_lock body"
                            )


# ---------------------------------------------------------------------------
# Out-of-scope modules do not touch writer_lock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("owner_file", [
    _REPO_ROOT / "apps" / "api" / "worker.py",
    _SRC_ROOT / "scripts_import" / "import_corporate_actions_common.py",
])
def test_process_scan_follows_returned_lock_context(source_inventory, owner_file):
    tree = ast.parse(
        "from algotrader_api.ingestion.writer_lock import writer_lock as acquire\n"
        "import subprocess\n"
        "def lock_factory(db):\n    return acquire(db, role='dividends', phase='dividends')\n"
        "with lock_factory(db):\n    subprocess.run([])\n"
    )
    sources = {**source_inventory, owner_file: (tree, _lock_acquisition_names(tree, owner_file))}
    with pytest.raises(pytest.fail.Exception, match="forbidden subprocess.run"):
        test_no_process_creation_inside_writer_lock_body(sources)


@pytest.mark.parametrize(
    "path",
    OUT_OF_SCOPE_MODULES,
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_out_of_scope_does_not_import_writer_lock(path):
    assert path.exists(), f"missing inventory file {path}"
    text = path.read_text()
    assert "writer_lock" not in text, (
        f"{path.relative_to(_REPO_ROOT)} mentions writer_lock — out-of-scope"
    )


@pytest.mark.parametrize(
    "path",
    OUT_OF_SCOPE_MODULES,
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_out_of_scope_does_not_import_writer_lock_module(path):
    """No `from ... import writer_lock` style imports."""
    assert path.exists(), f"missing inventory file {path}"
    tree = _parse(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert "writer_lock" not in alias.name, (
                    f"{path.relative_to(_REPO_ROOT)} imports writer_lock"
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "writer_lock" not in alias.name, (
                    f"{path.relative_to(_REPO_ROOT)} imports writer_lock"
                )


@pytest.mark.parametrize("name,path", list(NON_OWNING_FUNCTIONS.items()),
                         ids=list(NON_OWNING_FUNCTIONS))
def test_non_owner_does_not_acquire_writer_lock(name, path, source_inventory):
    tree, names = source_inventory[path]
    node = _qualified_function(tree, name)
    assert not _acquisition_sites(node, names), f"{path}:{name} acquires writer lock"


@pytest.mark.parametrize("name,path", list(BORROWED_ADJUSTMENT_FUNCTIONS.items()),
                         ids=list(BORROWED_ADJUSTMENT_FUNCTIONS))
def test_adjustment_borrower_does_not_own_transaction(name, path, source_inventory):
    tree, names = source_inventory[path]
    node = _qualified_function(tree, name)
    assert not _acquisition_sites(node, names), f"borrower {name} acquires writer lock"
    forbidden = [n.func.attr for n in ast.walk(node) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr in {"commit", "rollback", "close"}]
    assert not forbidden, f"borrower {name} takes transaction ownership: {forbidden}"