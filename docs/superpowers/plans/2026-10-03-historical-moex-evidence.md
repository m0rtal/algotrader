# Historical MOEX Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist zero-trade evidence from historical MOEX ISS
responses only when the upstream response is a complete paginated
fetch whose zero-trade rows sit on real business dates and match the
figi’s instrument identity. Degraded responses are logged but never
recorded; bar-list consumer behaviour stays unchanged.

**Architecture:** Two small, narrow surfaces. `_fetch_year_moex`
returns a typed `MOEXFetchOutcome` alongside the parsed rows
(bar-list behaviour preserved). A new helper
`record_historical_no_trade_evidence` gates on outcome and on a
business-date / identity filter, then delegates the actual SQL to the
existing `record_no_trade_evidence` so TTL semantics,
real-bar-wins, and ON CONFLICT refresh stay verbatim. Writer lock is
acquired once via the existing `_evidence_writer_lock` helper.

**Tech Stack:** Python 3.11 stdlib (`sqlite3`, `urllib.parse`,
`requests` already in use; `pytest`; SQLite WAL). No new
dependencies; no migration; no new lock path; no new role/phase.

**Spec:**
- `openspec/changes/persist-historical-moex-evidence/specs/data-quality/spec.md`
  (this change; validated with
  `openspec validate persist-historical-moex-evidence --strict`).
- `openspec/specs/writer-coordination/spec.md` (lock contract
  unchanged; reused as-is).
- `openspec/specs/data-quality/spec.md` (pre-existing
  `Pre-Consumption Coverage Gate`, `Per-figi no-trade evidence`,
  `Historical splits are derived from local bars` stay unchanged —
  no MODIFIED Requirement).

**Command root:** Run every command below from the repository worktree
root. The API interpreter is
`apps/api/.venv/bin/python` (a worktree-local symlink to the
already-provisioned project venv). The implementation base is the
current branch `fix/historical-moex-evidence` (HEAD `3ad3977`); a
preflight check confirms the branch and the active plan path before
Task 1.

**Preflight (run before Task 1):**

```bash
test "$(git -C /home/hermes/worktrees/algotrader-historical-moex-evidence branch --show-current)" = fix/historical-moex-evidence
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -c "import sys; print(sys.executable)"
openspec validate persist-historical-moex-evidence --strict --no-interactive
```

## Global Constraints

- Work only on `fix/historical-moex-evidence`; never commit or push
  `main`.
- TDD is mandatory: run each named RED test and confirm the expected
  failure before production code.
- Use the project Python with `PYTHONPATH` and `PYTHONHOME` unset.
- No new dependency, no new migration, no new lock path, no new
  role/phase.
- `_fetch_year_moex` outcome is the single source of truth; the
  existing bar consumer keeps its current list with no behavior
  change.
- `record_no_trade_evidence` (TTL, recent-vs-historical expiry,
  real-bar-wins, ON CONFLICT) is reused verbatim — no copy-edit.
- Tests use file-backed temporary SQLite databases for every lock
  assertion; never the production DB.
- Do not read, print, commit, or summarize secrets / broker
  payloads / request bodies / DB contents.
- Each task ends in one focused commit and an independent spec +
  quality review before the next task.

---

### Task 1: Fetcher outcome contract and bar-list compatibility

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
  (add the `MOEXFetchOutcome` `Literal` re-export)
- Modify: `apps/api/tests/test_moex_no_trade_evidence.py`
  (add a focused new section; do NOT mutate existing scenario
  assertions)
- Modify: `apps/api/tests/test_moex_all_paths_identity.py`
  (regression test)

**Interfaces:**
- `MOEXFetchOutcome = Literal["complete", "partial", "error",
  "malformed", "identity_mismatch"]` lives in
  `apps.api.ingestion.no_trade_evidence` and is re-exported by
  `apps.api.ingestion.backfill`.
- `apps.api.ingestion.backfill._fetch_year_moex(market, board,
  ticker, year, *, last_trading_day=None) -> list[dict]` stays
  exactly as it is for the bar consumer (positional-or-keyword
  signature, same return shape).
- `apps.api.ingestion.backfill._fetch_year_moex_outcome(market,
  board, ticker, year, *, last_trading_day=None) -> tuple[list[dict],
  MOEXFetchOutcome]` is the new variant that returns the outcome;
  it walks the same pages and re-uses a small
  `_reduce_outcomes` helper internally.
- Existing callers (`_fetch_moex_range`, `_process_moex_year`,
  `_process_one`, `backfill_moex_recent_tail`) keep calling the
  list-only variant. The evidence consumers call the new variant.

**Step 1: RED — outcome classification**

In `apps/api/tests/test_moex_no_trade_evidence.py`, add a new
section at the end (preserving the existing fixtures):

```python
# -- MOEXFetchOutcome (Task 1) -------------------------------------------


def test_fetch_year_moex_outcome_complete_full_pagination():
    """Single-page full cursor yields outcome == 'complete'."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-28", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000],
            ],
        },
        "history.cursor": {"data": [[2, 2, 500]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("complete")
    assert len(rows) == 2


def test_fetch_year_moex_outcome_error_on_network_failure():
    """requests.get raising -> outcome == 'error', rows == []."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    import unittest.mock as _mock
    def boom(*a, **kw):
        raise ConnectionError("net")
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=boom):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("error")
    assert rows == []


def test_fetch_year_moex_outcome_malformed_missing_tradedate():
    """history.columns missing TRADEDATE -> 'malformed'."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["SECID", "BOARDID", "OPEN", "CLOSE"],
            "data": [["GAZP", "TQBR", 100, 101]],
        },
        # No history.cursor block, short page.
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("malformed")
    assert rows == []


def test_fetch_year_moex_outcome_short_page_no_cursor_is_malformed():
    """No cursor + short page cannot certify completeness."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-28", "GAZP", "TQBR"]],
        },
        # No history.cursor; len(rows) < page_size (500).
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("malformed")


def test_fetch_year_moex_outcome_partial_incomplete_cursor():
    """cursor offset + len < total -> 'partial'."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-28", "GAZP", "TQBR"]] * 500,
        },
        "history.cursor": {"data": [[0, 1000, 500]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("partial")
    assert len(rows) == 500


def test_fetch_year_moex_outcome_identity_mismatch_is_row_check():
    """A single cross-listed mirror row poisons the whole fetch."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [
                ["2025-09-28", "GAZP", "TQBR"],
                ["2025-09-29", "GAZP", "TQBR"],
                ["2025-09-30", "SBER", "TQBR"],  # identity mismatch
            ],
        },
        "history.cursor": {"data": [[3, 3, 500]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("identity_mismatch")
    assert len(rows) == 3


def test_fetch_year_moex_outcome_worst_severity_wins():
    """First page error forces outcome == 'error' even if subsequent pages parse."""
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    call_count = {"n": 0}

    def fake_get(url, params=None, timeout=None):  # noqa: ARG001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ConnectionError("net")
        return _FakeResp({
            "history": {
                "columns": ["TRADEDATE", "SECID", "BOARDID"],
                "data": [["2025-09-28", "GAZP", "TQBR"]],
            },
            "history.cursor": {"data": [[1, 1, 500]]},
        })

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=fake_get):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("error")
    assert rows == []
```

Run `apps/api/tests/test_moex_no_trade_evidence.py` and confirm
seven RED failures with `AttributeError: module
'algotrader_api.ingestion.backfill' has no attribute
'_fetch_year_moex_outcome'` and `ImportError: cannot import name
'MOEXFetchOutcome'`.

**Step 2: RED — bar-list compatibility**

Existing tests that use `backfill._fetch_year_moex` MUST keep
passing unchanged. Add a regression test:

```python
def test_fetch_year_moex_list_only_signature_unchanged():
    """Existing positional-or-keyword callers continue to work."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-28", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[1, 1, 500]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows = backfill._fetch_year_moex(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_boardid"] == "TQBR"
```

Run that single test; expected PASS today (proves existing
behaviour is preserved) and that the implementation in Step 4
keeps it that way.

**Step 3: RED — outcome carried for existing recent-tail test**

In `apps/api/tests/test_moex_no_trade_evidence.py`, add:

```python
def test_fetch_year_moex_emits_raw_columns_per_dict_still_passes_after_outcome():
    """Regression: the existing raw-columns test must still pass after
    the outcome addition."""
    # Re-uses the same fake payload as
    # test_fetch_year_moex_emits_raw_columns_per_dict but verifies
    # both the list-only and the new outcome variant agree on shape.
    from algotrader_api.ingestion import backfill
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
    )

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-28", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[1, 1, 500]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == MOEXFetchOutcome("complete")
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_numtrades"] == 0
```

Run the existing `test_fetch_year_moex_emits_raw_columns_per_dict`
test in `apps/api/tests/test_moex_no_trade_evidence.py`; expected
PASS today. After Step 4 it MUST still PASS.

**Step 4: GREEN — implement the outcome variant**

In `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`,
add at module scope (above the existing helpers):

```python
from typing import Literal

# Fetch outcome emitted by ``_fetch_year_moex_outcome``. Decision
# rule (see ADDED Requirement in
# openspec/changes/persist-historical-moex-evidence): one value per
# fetch, worst-severity across pages.
MOEXFetchOutcome = Literal[
    "complete", "partial", "error", "malformed", "identity_mismatch",
]
```

In `apps/api/src/algotrader_api/ingestion/backfill.py`:

1. Import `MOEXFetchOutcome` from `.no_trade_evidence` at the top of
   the file (after the existing imports).
2. Add a `_reduce_outcomes(prev, new) -> MOEXFetchOutcome` private
   helper with the severity ordering
   `error > malformed > partial > identity_mismatch > complete`
   (return the more severe of the two). Keep it private to the
   module.
3. Refactor `_fetch_year_moex` to a thin wrapper that delegates the
   loop to a private `_fetch_year_moex_iter(market, board, ticker,
   year, *, last_trading_day=None) -> Iterator[tuple[list[dict],
   MOEXFetchOutcome]]`. The wrapper consumes the iterator and
   returns `list[dict]`, preserving the exact existing signature.
4. Add `_fetch_year_moex_outcome(market, board, ticker, year, *,
   last_trading_day=None) -> tuple[list[dict], MOEXFetchOutcome]` as
   a sibling that returns both. It walks the same iterator and
   collects all parsed rows plus the worst-severity outcome.
5. Inside `_fetch_year_moex_iter`:
   - On any HTTP / network exception in the `requests.get` call,
     yield `([], "error")` and stop.
   - On a response whose `history.columns` is empty or does not
     contain `TRADEDATE`, yield `([], "malformed")` and stop.
   - For each parsed row, check `SECID == ticker` and
     `BOARDID == board`. Track an `identity_mismatch` flag if any
     row fails the check. Rows that fail the identity check are
     still appended to the row list (preserves bar-list consumer
     compatibility).
   - On the cursor:
     - If present and well-formed, compute
       `offset + len(rows) < total`. If true, mark `partial`.
     - If absent and `len(rows) < page_size`, mark `malformed`.
     - If absent and `len(rows) >= page_size`, mark `partial`
       (cannot certify completeness without a cursor).
   - Yield after each page. The caller picks the worst severity.

Preserve all existing per-dict fields (`figi`, `ts`, `open`, `high`,
`low`, `close`, `volume`, `source`, `_secid`, `_boardid`,
`_numtrades`, `_value`).

**Step 5: GREEN — re-export the literal and verify**

```bash
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_moex_recent_tail_fixes.py \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_backfill_metadata_poison.py -q
```

Expected: all green. The seven new tests in Step 1 now PASS; the
two new tests in Steps 2 and 3 PASS; the existing
`test_fetch_year_moex_emits_raw_columns_per_dict` and the
identity-test file PASS unchanged.

**Step 6: Verify and commit**

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
git diff --check
git branch --show-current
git add apps/api/src/algotrader_api/ingestion/backfill.py \
        apps/api/src/algotrader_api/ingestion/no_trade_evidence.py \
        apps/api/tests/test_moex_no_trade_evidence.py \
        apps/api/tests/test_moex_all_paths_identity.py
git commit -m "feat(ingestion): emit MOEXFetchOutcome alongside _fetch_year_moex rows"
```

Reviewer passes only if spec compliance ✅ and task quality
approved; no Critical/Important findings. The bar consumer keeps
its existing list; the outcome is the new addition.

---

### Task 2: Historical evidence helper, business-date filter, CLI outcome gate

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
- Modify: `apps/api/scripts/backfill_no_trade_evidence.py`
- Modify: `apps/api/tests/test_moex_no_trade_evidence.py`
- Modify: `apps/api/tests/test_backfill_no_trade_evidence_lock.py`
- Modify: `apps/api/tests/test_moex_all_paths_identity.py`

**Interfaces:**
- `apps.api.ingestion.no_trade_evidence.MOEXFetchOutcome` — already
  introduced in Task 1.
- `apps.api.ingestion.no_trade_evidence._is_business_date_for_evidence(
  conn, ts: str, *, today: date | None = None) -> bool` — new private
  helper. Returns `True` iff `ts` parses as an ISO date, is a
  weekday, and is not present in `moex_holidays` for the supplied
  `conn`. `today` is used only to clamp the holiday scan range.
- `apps.api.ingestion.no_trade_evidence.record_historical_no_trade_evidence(
  conn, *, db_path, figi, rows, board, isin, outcome, today=None) -> int`
  — new public wrapper.
  - `outcome` MUST be the `MOEXFetchOutcome` returned by
    `_fetch_year_moex_outcome` for the batch. Any value other than
    `"complete"` short-circuits: returns `0`, performs no SQLite
    mutation, and emits one structured log line via `logging` (the
    project’s CLI uses stdlib `print` for structured output; the
    helper uses `logging.getLogger("algotrader.ingestion").info` so
    `apps/api/scripts/backfill_no_trade_evidence.py` can capture
    via a stream handler).
  - When `outcome == "complete"`, the helper:
    1. Filters `rows` to those whose `_secid == ticker` (passed
       via the caller) and `_boardid == board`; mismatches are
       dropped silently (they were already filtered by the
       outcome rule at the fetcher level; this is a defensive
       second check).
    2. Filters `rows` to those whose `ts` parses as an ISO date
       and is a business date per
       `_is_business_date_for_evidence`. Rows that fail this
       check are dropped from the persisted set but counted in
       `non_business_date` rejections.
    3. Delegates the actual `INSERT … ON CONFLICT` to the
       existing `record_no_trade_evidence` with the filtered
       rows. TTL semantics, recent-vs-historical expiry
       branching, real-bar-wins filtering, and ON CONFLICT
       refresh behaviour are preserved verbatim.
- `apps.api.scripts.backfill_no_trade_evidence.main()`:
  - Calls the new `_fetch_year_moex_outcome` for each figi instead
    of `_fetch_year_moex`.
  - Branches on outcome. For non-`complete`, emits exactly one
    structured log line per figi via `print(...)`:
    `moex_historical_evidence_rejected figi={figi} ticker={ticker}
    reason={reason} rows={n}`.
  - For `complete`, calls
    `record_historical_no_trade_evidence(...)` with the explicit
    `db_path`.

**Step 1: RED — outcome gating**

In `apps/api/tests/test_moex_no_trade_evidence.py`, add:

```python
def test_record_historical_no_trade_evidence_rejects_partial(tmp_path):
    """Partial outcome short-circuits and writes nothing."""
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    rows = [
        {"ts": "2025-09-28", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=rows, board="TQBR", isin="RU000GAZP",
        outcome=MOEXFetchOutcome("partial"),
    )
    assert written == 0
    n = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n == 0


def test_record_historical_no_trade_evidence_rejects_error(
    tmp_path,
):
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=[{"ts": "2025-09-28"}], board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("error"),
    )
    assert written == 0


def test_record_historical_no_trade_evidence_rejects_malformed(
    tmp_path,
):
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=[{"ts": "2025-09-28"}], board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("malformed"),
    )
    assert written == 0


def test_record_historical_no_trade_evidence_rejects_identity_mismatch(
    tmp_path,
):
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    rows = [{"ts": "2025-09-28", "_secid": "SBER", "_boardid": "TQBR"}]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=rows, board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("identity_mismatch"),
    )
    assert written == 0
```

Run those four tests; expected RED with
`ImportError: cannot import name
'record_historical_no_trade_evidence'` (the helper does not exist
yet).

**Step 2: RED — business-date filter**

In the same file, add:

```python
def test_is_business_date_for_evidence_weekday_not_holiday(tmp_path):
    """A plain weekday with no holiday row is a business date."""
    from algotrader_api.ingestion.no_trade_evidence import (
        _is_business_date_for_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    # 2025-09-29 is a Monday.
    assert _is_business_date_for_evidence(
        con, "2025-09-29",
    )


def test_is_business_date_for_evidence_saturday_is_not(tmp_path):
    """2025-09-27 is a Saturday."""
    from algotrader_api.ingestion.no_trade_evidence import (
        _is_business_date_for_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    assert not _is_business_date_for_evidence(
        con, "2025-09-27",
    )


def test_is_business_date_for_evidence_holiday_is_not(tmp_path):
    """A weekday that is in moex_holidays is not a business date."""
    from algotrader_api.ingestion.no_trade_evidence import (
        _is_business_date_for_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    con.execute(
        "INSERT INTO moex_holidays(date, name) VALUES ('2025-09-29', 'X')"
    )
    con.commit()
    assert not _is_business_date_for_evidence(
        con, "2025-09-29",
    )


def test_is_business_date_for_evidence_malformed_ts_is_not(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        _is_business_date_for_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    assert not _is_business_date_for_evidence(con, "garbage")
    assert not _is_business_date_for_evidence(con, "")
    assert not _is_business_date_for_evidence(con, "2025-9-29")
```

Run those four tests; expected RED with
`ImportError: cannot import name
'_is_business_date_for_evidence'`.

**Step 3: RED — complete outcome with business-date filter**

In the same file, add:

```python
def test_record_historical_no_trade_evidence_complete_filters_non_business(
    tmp_path,
):
    """Complete outcome + mixed business / non-business rows -> only
    business-date rows are persisted."""
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    # Seed a holiday on the weekday test row.
    con.execute(
        "INSERT INTO moex_holidays(date, name) VALUES ('2025-09-29', 'X')"
    )
    con.commit()
    rows = [
        {"ts": "2025-09-29", "_secid": "GAZP", "_boardid": "TQBR"},  # holiday
        {"ts": "2025-09-27", "_secid": "GAZP", "_boardid": "TQBR"},  # Sat
        {"ts": "2025-09-30", "_secid": "GAZP", "_boardid": "TQBR"},  # Tue ok
        {"ts": "garbage",   "_secid": "GAZP", "_boardid": "TQBR"},  # bad
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=rows, board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("complete"),
    )
    assert written == 1
    rows_db = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence WHERE figi='FIGI1'"
    ).fetchall()
    assert [r["session_date"] for r in rows_db] == ["2025-09-30"]


def test_record_historical_no_trade_evidence_complete_keeps_ttl_semantics(
    tmp_path,
):
    """Recent row gets 7-day expiry, historical row gets 365-day
    expiry."""
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        RECENT_EVIDENCE_EXPIRY,
        HISTORICAL_EVIDENCE_EXPIRY,
        record_historical_no_trade_evidence,
    )
    import datetime as _dt

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    today = _dt.date(2026, 9, 30)
    rows = [
        # Recent (today - 5 days): 2026-09-25.
        {"ts": "2026-09-25", "_secid": "GAZP", "_boardid": "TQBR"},
        # Historical (today - 60 days): 2026-08-01.
        {"ts": "2026-08-01", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=rows, board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("complete"),
        today=today,
    )
    recent = con.execute(
        "SELECT expires_at FROM moex_no_trade_evidence "
        "WHERE session_date = '2026-09-25'"
    ).fetchone()[0]
    historical = con.execute(
        "SELECT expires_at FROM moex_no_trade_evidence "
        "WHERE session_date = '2026-08-01'"
    ).fetchone()[0]
    assert recent == (today + RECENT_EVIDENCE_EXPIRY).isoformat()
    assert historical == (today + HISTORICAL_EVIDENCE_EXPIRY).isoformat()


def test_record_historical_no_trade_evidence_complete_respects_real_bar(
    tmp_path,
):
    """A real bar for the same date suppresses the evidence row."""
    from algotrader_api.ingestion.no_trade_evidence import (
        MOEXFetchOutcome,
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    _insert_bar(con, "FIGI1", "2025-09-30", close=100)
    rows = [
        {"ts": "2025-09-30", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1",
        rows=rows, board="TQBR", isin="RU",
        outcome=MOEXFetchOutcome("complete"),
    )
    assert written == 0
    n = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n == 0
```

Run those three tests; expected RED with `ImportError`.

**Step 4: GREEN — implement the helper**

In `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`,
add the two new helpers ABOVE the existing
`record_no_trade_evidence` (the existing wrapper stays untouched):

```python
def _is_business_date_for_evidence(
    conn: sqlite3.Connection,
    ts: str,
    *,
    today: date | None = None,
) -> bool:
    """True iff ``ts`` is an ISO date, a weekday, and not in
    ``moex_holidays``.

    Pure SQL helper. Does NOT acquire the writer lock; callers that
    persist rows must already hold it. Operates on the
    caller's open connection so a single transaction sees both
    the holiday table and the evidence table.
    """
    try:
        d = date.fromisoformat((ts or "")[:10])
    except ValueError:
        return False
    if d.weekday() >= 5:  # Saturday / Sunday.
        return False
    cap = today or date.today()
    # Scan only the holidays inside [d, d]. We could expand this to
    # the full window, but a per-row scan stays cheap for historical
    # evidence batches (≤ ~500 rows per year).
    row = conn.execute(
        "SELECT 1 FROM moex_holidays WHERE date = ?",
        (d.isoformat(),),
    ).fetchone()
    return row is None
```

Then add the public wrapper, sitting between the private
`_record_no_trade_evidence_tx` and the existing public
`record_no_trade_evidence`. The new helper:

```python
def record_historical_no_trade_evidence(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    figi: str,
    rows: list[dict],
    board: str,
    isin: str,
    outcome: MOEXFetchOutcome,
    today: date | None = None,
) -> int:
    """Persist zero-trade evidence for a historical MOEX walk.

    Outcome gate:
      * ``outcome == "complete"`` — proceed to the business-date /
        identity filter and delegate to ``record_no_trade_evidence``.
      * any other outcome — return ``0`` immediately, perform no
        SQLite mutation, and emit one structured log line.

    Filter rule (after outcome gate):
      * keep rows whose ``_secid`` matches the caller's SECID (the
        upstream already enforces this; this is defensive);
      * keep rows whose ``_boardid`` matches ``board``;
      * keep rows whose ``ts`` is a valid ISO date AND a business
        date per :func:`_is_business_date_for_evidence`.

    Writer lock: acquired exactly once through
    :func:`_evidence_writer_lock` with
    ``role="no-trade-evidence"`` and ``phase="evidence"`` (the
    same role/phase pair the existing wrapper uses). The private
    :func:`_record_no_trade_evidence_tx` does NOT re-acquire the
    lock.

    Delegation: the actual ``INSERT ... ON CONFLICT`` is performed
    by the existing :func:`record_no_trade_evidence` so TTL,
    recent-vs-historical expiry branching, real-bar-wins
    filtering, and ON CONFLICT refresh behaviour stay verbatim.
    """
    if outcome != "complete":
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s outcome=%s rows=%s",
            figi, outcome, len(rows),
        )
        return 0
    if not rows:
        return 0
    secid = str(rows[0].get("_secid") or "")
    accepted: list[dict] = []
    for r in rows:
        if str(r.get("_secid") or "") != secid:
            continue
        if str(r.get("_boardid") or "") != board:
            continue
        ts = (r.get("ts") or "")[:10]
        if not _is_business_date_for_evidence(conn, ts, today=today):
            continue
        accepted.append({"ts": ts})
    if not accepted:
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s "
            "reason=non_business_date rows=%s",
            figi, len(rows) - len(accepted),
        )
        return 0
    return record_no_trade_evidence(
        conn,
        db_path=db_path,
        figi=figi,
        rows=accepted,
        board=board,
        isin=isin,
        today=today,
    )
```

Add `import logging` at the top of the file if not already
imported. Re-run the RED tests from Steps 1–3. Expected GREEN.

**Step 5: RED/GREEN — wire the CLI to the outcome**

In `apps/api/tests/test_backfill_no_trade_evidence_lock.py`, add:

```python
def test_cli_partial_outcome_logs_and_writes_zero(tmp_path,
                                                  monkeypatch, caplog):
    """Stubbed partial outcome leaves evidence untouched and emits
    the structured rejection line."""
    import logging as _logging
    from algotrader_api.scripts import backfill_no_trade_evidence as cli
    from algotrader_api.ingestion import backfill as backfill_mod

    db = tmp_path / "cli.db"
    con = _make_conn(db)  # reuse the test fixture helper.
    con.execute(
        "INSERT INTO instruments(figi, ticker, isin, source_updated_at, "
        "expected_bars) VALUES ('FIGI1', 'GAZP', 'RU', '2025-01-01', 250)"
    )
    con.execute(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) "
        "VALUES ('FIGI1', '2025-08-20', 100, 102, 99, 101, 1000)"
    )
    con.commit()

    def fake_fetch(market, board, ticker, from_d, to_d, *,
                   last_trading_day=None):  # noqa: ARG001
        # Ignored by the new helper; only ``_fetch_year_moex_outcome``
        # drives the outcome.
        return []

    def fake_outcome(market, board, ticker, year, *,
                     last_trading_day=None):  # noqa: ARG001
        from algotrader_api.ingestion.no_trade_evidence import (
            MOEXFetchOutcome,
        )
        return ([], MOEXFetchOutcome("partial"))

    monkeypatch.setattr(backfill_mod, "_fetch_moex_range", fake_fetch)
    monkeypatch.setattr(backfill_mod, "_fetch_year_moex_outcome",
                        fake_outcome)
    monkeypatch.setattr(cli, "_fetch_moex_range", fake_fetch)
    monkeypatch.setattr(cli, "_fetch_year_moex_outcome", fake_outcome)

    rc = cli.main_with_args([
        "--db", str(db),
        "--days", "60",
    ])
    assert rc == 0
    n = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n == 0
```

This test requires a `main_with_args(argv) -> int` shim on the CLI
that is wired in Step 6 (the CLI currently uses `argparse` directly
inside `main()`). Implement that shim and route `main()` through it.

Run the test; expected RED initially, GREEN after Step 6.

**Step 6: GREEN — wire the CLI**

In `apps/api/scripts/backfill_no_trade_evidence.py`:

1. Import `_fetch_year_moex_outcome` and `MOEXFetchOutcome` from
   `algotrader_api.ingestion.backfill` /
   `algotrader_api.ingestion.no_trade_evidence`.
2. Replace the existing per-figi block that calls
   `_fetch_moex_range` + `_extract_zero_trade_rows` with a block
   that:
   - Calls `_fetch_year_moex_outcome(market, board, ticker, year)`
     for each touched calendar year.
   - Aggregates the rows across years and reduces the outcome to
     the worst severity across pages (use
     `backfill._reduce_outcomes`).
   - On non-`complete`, emits exactly one
     `moex_historical_evidence_rejected figi={figi} ...` line and
     continues to the next figi.
   - On `complete`, delegates to
     `record_historical_no_trade_evidence` with the explicit
     `db_path`.
3. Add the `main_with_args(argv: list[str]) -> int` shim so tests
   can drive it without forking a subprocess. `main()` becomes:

   ```python
   def main() -> int:
       return main_with_args(sys.argv[1:])
   ```

Re-run the test from Step 5; expected GREEN. Re-run the existing
`test_backfill_no_trade_evidence_lock.py` suite; expected PASS
unchanged.

**Step 7: Verify and commit**

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
git diff --check
git branch --show-current
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_moex_recent_tail_fixes.py -q
git add apps/api/src/algotrader_api/ingestion/no_trade_evidence.py \
        apps/api/scripts/backfill_no_trade_evidence.py \
        apps/api/tests/test_moex_no_trade_evidence.py \
        apps/api/tests/test_backfill_no_trade_evidence_lock.py \
        apps/api/tests/test_moex_all_paths_identity.py
git commit -m "feat(ingestion): gate historical zero-trade evidence on validated complete fetches"
```

Reviewer passes only if:
- The four outcome-rejection tests in Step 1 PASS (partial / error
  / malformed / identity_mismatch).
- The four business-date tests in Step 2 PASS.
- The three complete-outcome tests in Step 3 PASS (filter,
  TTL preservation, real-bar-wins).
- The CLI test in Step 5 PASSes.
- The existing `record_no_trade_evidence` tests PASS unchanged
  (TTL semantics preserved).
- The existing `_fetch_year_moex` tests PASS unchanged
  (bar-list consumer compatibility preserved).

---

### Task 3 (optional, independently reviewable): in-process historical walker uses outcome gate

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
  (`BackfillRunner._process_moex_year` /
  `BackfillRunner._process_one` historical walk branch)
- Modify: `apps/api/tests/test_backfill_source_routing.py`
- Modify: `apps/api/tests/test_backfill_metadata_poison.py`

**Interfaces:**
- `BackfillRunner._process_moex_year` and `_process_one` (historical
  walk branch) now call `_fetch_year_moex_outcome` and pass the
  outcome to a new evidence helper call. The bar list passed to the
  downstream `replace_bars_for_figi` is unchanged.
- Evidence in the historical walker is gated by outcome; real-bar
  writes are NOT.

**Step 1: RED — partial historical fetch leaves evidence untouched**

In `apps/api/tests/test_backfill_source_routing.py`, add a test that
exercises the historical walker path with a stubbed partial
outcome and asserts that no `moex_no_trade_evidence` row is written
but real bars (returned by the partial response) ARE written. Use the
same `_make_conn` / figi fixtures already in the file.

Run the test; expected RED with
`ImportError: cannot import name ...` or
`AttributeError: ...` (the walker does not yet know about
outcome).

**Step 2: GREEN — wire the walker**

In `apps/api/src/algotrader_api/ingestion/backfill.py`
`BackfillRunner._process_moex_year` / `_process_one`:

1. Replace the call to `self._fetch_year_moex(...)` with
   `self._fetch_year_moex_outcome(...)`.
2. Unpack the tuple into `bars, outcome`.
3. Feed `bars` to the existing bar write path unchanged.
4. On `outcome == "complete"`, call
   `record_historical_no_trade_evidence(...)` from
   `algotrader_api.ingestion.no_trade_evidence` with the explicit
   `self.db_path`. On any other outcome, log one structured line
   (`{event: "moex_historical_evidence_rejected", figi, ticker,
   reason, rows}`) and continue.
5. Never let the outcome gate affect `bars` or `replace_bars_for_figi`.

Re-run the test from Step 1; expected GREEN. Re-run the existing
`test_backfill_source_routing.py`,
`test_backfill_metadata_poison.py`, and
`test_backfill_coverage.py`; expected PASS unchanged.

**Step 3: Verify and commit**

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
git diff --check
git branch --show-current
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_backfill_metadata_poison.py \
  apps/api/tests/test_backfill_coverage.py \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py -q
git add apps/api/src/algotrader_api/ingestion/backfill.py \
        apps/api/tests/test_backfill_source_routing.py \
        apps/api/tests/test_backfill_metadata_poison.py
git commit -m "feat(ingestion): apply outcome gate to in-process historical walker"
```

Reviewer verifies:
- Partial / error / malformed / identity_mismatch outcomes leave
  `moex_no_trade_evidence` untouched.
- `bars` table continues to receive whatever real bars the partial
  response had (existing partial-bar consumer behaviour).
- Identity / ISIN / SECID / BOARDID checks remain unchanged.
- Writer-coordination lock contract unchanged: the evidence
  write-section acquires the shared lock once via the existing
  `_evidence_writer_lock` helper. No new lock path, no new
  role/phase.

---

### End-to-end smoke (after Tasks 1–3 land)

Run the focused regression set and the broader safe regression set,
identical to the standing convention:

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_moex_recent_tail_fixes.py \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_backfill_metadata_poison.py \
  apps/api/tests/test_backfill_coverage.py \
  apps/api/tests/test_catchup_same_day.py \
  apps/api/tests/test_worker_daily_chain.py \
  apps/api/tests/test_writer_lock.py -q
```

Expected: all green; no new failures vs. the recorded base
(commit `3ad3979`). Bar counts in the existing partial-bar tests
remain identical.