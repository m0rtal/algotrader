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
"""
from __future__ import annotations

import ast
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

OUT_OF_SCOPE_MODULES = [
    _SRC_ROOT / "pipeline" / "assertions.py",
    _SRC_ROOT / "data_quality" / "service.py",
    _SRC_ROOT / "data_quality" / "completeness.py",
    _SRC_ROOT / "maintenance" / "cleanup.py",
    _INGEST / "universe.py",
    _INGEST / "universe_sync.py",
    _SRC_ROOT / "scripts_import" / "import_corporate_actions.py",
    _SRC_ROOT / "scripts_import" / "import_dividends_tinkoff.py",
]


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


def _writer_lock_calls(tree: ast.Module) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "writer_lock"
    ]


def _enclosing_with_for_call(tree: ast.Module, target: ast.AST):
    """Find the `with` statement whose body contains `target`, if any."""
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for sub in ast.walk(node):
                if sub is target:
                    return node
    return None


def test_no_process_creation_inside_writer_lock_body():
    """No `os.fork`, `subprocess`, or `multiprocessing` calls inside
    any `with writer_lock(...)` body.
    """
    forbidden_names = {"fork", "Popen", "run", "spawn", "Process"}
    forbidden_modules = {"os", "subprocess", "multiprocessing"}

    for path in IN_SCOPE_FUNCTIONS.values():
        if path.suffix != ".py":
            continue
        tree = _parse(path)
        for call in _writer_lock_calls(tree):
            ctx = _enclosing_with_for_call(tree, call)
            assert ctx is not None, "writer_lock(...) must be inside a `with`"
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


@pytest.mark.parametrize(
    "path",
    [p for p in OUT_OF_SCOPE_MODULES if p.exists()],
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_out_of_scope_does_not_import_writer_lock(path):
    text = path.read_text()
    assert "writer_lock" not in text, (
        f"{path.relative_to(_REPO_ROOT)} mentions writer_lock — out-of-scope"
    )


@pytest.mark.parametrize(
    "path",
    [p for p in OUT_OF_SCOPE_MODULES if p.exists()],
    ids=lambda p: str(p.relative_to(_REPO_ROOT)),
)
def test_out_of_scope_does_not_import_writer_lock_module(path):
    """No `from ... import writer_lock` style imports."""
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