# Historical MOEX Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist zero-trade evidence from historical MOEX ISS
responses only when the upstream response is a complete paginated
fetch whose zero-trade rows sit on real business dates and match the
figi's instrument identity. Degraded responses are logged but never
recorded; bar-list consumer behaviour stays unchanged. The in-process
historical walker (`BackfillRunner._process_moex_year` and
`_process_one` historical walk branch) is bound to the new outcome
contract and the new evidence helper — the walker integration is
**mandatory**, not optional.

**Architecture:** Three small, narrow surfaces.

1. `backfill._fetch_year_moex_outcome` returns a typed
   `MOEXFetchOutcome` alongside the parsed rows. The legacy
   `backfill._fetch_year_moex` is preserved as a thin wrapper
   (positional-or-keyword signature, exact same return shape) so
   existing bar consumers keep working unchanged.
2. `no_trade_evidence.record_historical_no_trade_evidence` gates
   on outcome, on a business-date filter, and on a per-row
   SECID/BOARDID identity check that uses the caller's explicit
   `ticker` argument (not a guess from `rows[0]`). The actual
   `INSERT … ON CONFLICT` is delegated to the existing
   `record_no_trade_evidence` so TTL semantics, real-bar-wins,
   and ON CONFLICT refresh stay verbatim.
3. The in-process historical walker is statically bound to the
   new fetch symbol (`runner._fetch_year_moex_outcome`) and
   routes only `complete` outcomes to the new helper. Real-bar
   writes go through the existing `replace_bars_for_figi` path
   unchanged. The historical CLI
   `apps/api/scripts/backfill_no_trade_evidence.py` is a thin
   call-through to the new helper — it picks up the outcome
   gate, the business-date filter, and the structured
   `moex_historical_evidence_rejected` line by routing through
   the new helper, with no new argument surface, no new exit
   code path, and no new branch.

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
- `MOEXFetchOutcome` is a `typing.Literal` alias declared in
  `no_trade_evidence.py` and re-exported (NOT re-declared) by
  `backfill.py`. Tests use plain string comparisons
  (`outcome == "complete"`); never `MOEXFetchOutcome("complete")`.
- The legacy `backfill._fetch_year_moex(market, board, ticker,
  year, last_trading_day=None)` signature is **positional-or-keyword**
  (no `*` separator before `last_trading_day`); the new
  `_fetch_year_moex_outcome` mirrors the same shape. Existing
  callers that pass `last_trading_day` positionally keep working.
- `_fetch_year_moex_outcome` is the single source of truth; the
  existing bar consumer keeps its current list with no behaviour
  change.
- `record_no_trade_evidence` (TTL, recent-vs-historical expiry,
  real-bar-wins, ON CONFLICT) is reused verbatim — no copy-edit.
- `record_historical_no_trade_evidence` accepts an explicit
  `ticker` argument; the helper MUST NOT guess the ticker from
  `rows[0].get("_secid")`.
- Tests use file-backed temporary SQLite databases for every lock
  assertion; never the production DB.
- Do not read, print, commit, or summarize secrets / broker
  payloads / request bodies / DB contents.
- Each task ends in one focused commit and an independent spec +
  quality review before the next task.

---

### Task 1 (mandatory): Fetcher outcome contract + bar-list compatibility

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
  (add the `MOEXFetchOutcome` `Literal` declaration)
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
  (re-export the literal, add `_reduce_outcomes`,
  `_fetch_year_moex_outcome`, refactor `_fetch_year_moex` to a
  thin wrapper around a shared iterator; strict feed contract
  on the new loop)
- Modify: `apps/api/tests/test_moex_no_trade_evidence.py`
  (new section; do NOT mutate existing scenario assertions)
- Modify: `apps/api/tests/test_moex_all_paths_identity.py`
  (regression test)

**Interfaces:**
- `MOEXFetchOutcome = Literal["complete", "partial", "error",
  "malformed", "identity_mismatch"]` lives in
  `apps.api.ingestion.no_trade_evidence` and is re-exported by
  `apps.api.ingestion.backfill` via a module-level alias
  (`from .no_trade_evidence import MOEXFetchOutcome`); callers
  always import the same symbol — no re-declaration, no
  circular import.
- `apps.api.ingestion.backfill._fetch_year_moex(market, board,
  ticker, year, last_trading_day=None) -> list[dict]` is
  preserved verbatim (positional-or-keyword `last_trading_day`,
  exact same return shape, no behaviour change for the bar
  consumer). It is now a thin wrapper around a shared iterator
  that the new outcome-emitting variant also walks.
- `apps.api.ingestion.backfill._fetch_year_moex_outcome(market,
  board, ticker, year, last_trading_day=None) ->
  tuple[list[dict], MOEXFetchOutcome]` is the new variant that
  returns the outcome alongside the rows. It walks the same
  pages and re-uses a small `_reduce_outcomes` helper internally
  to track the worst-severity outcome across pages.
- Existing callers (`_fetch_moex_range`, `_process_moex_year`,
  `_process_one`, `backfill_moex_recent_tail`) keep calling the
  list-only variant. The evidence consumers (Task 2) call the
  new outcome variant.

**Strict feed contract (the fetcher MUST enforce at fetch time):**

For every page the loop reads:
- `response.status_code == 200` — otherwise outcome is
  `malformed` and the loop stops.
- `history.columns` contains `TRADEDATE` AND the four OHLC
  columns AND `VOLUME` — otherwise outcome is `malformed` and
  the loop stops.
- For every parsed row, `len(row) == len(cols)` — rows that are
  too short are dropped (and the page's outcome remains whatever
  the cursor / identity checks determine).
- For the explicit zero-trade shape (`OPEN=HIGH=LOW=CLOSE=None`,
  `VOLUME=0`), `NUMTRADES` and `VALUE` MUST be present and equal
  to `0`; a missing counter on a zero-trade row makes the page
  `malformed`.
- `history.cursor.data[0]` MUST carry `[offset, total,
  page_size]`; the loop records the requested `start` and
  compares it to the server-reported `offset` on every page.
  Mismatch → `malformed`. Repeated cursor offset across two
  pages → `malformed`. Non-positive `total` → `malformed`.
- Identity: at least one row with `SECID != ticker` OR
  `BOARDID != board` makes the outcome `identity_mismatch`
  (batch-poisoning; identity is per-batch).
- Final page completeness: `offset + len(rows) >= total`
  (using the cursor's own offset, total, and the page's
  parsed row count) makes the outcome `complete` if all other
  conditions hold. Otherwise, `partial`.

#### Step 1: RED — outcome classification

In `apps/api/tests/test_moex_no_trade_evidence.py`, add at the
end (existing fixtures stay):

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
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
                ["2025-09-30", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000],
            ],
        },
        # cursor: offset 0, total 2, page_size 2 (loop asked for 500,
        # the loop is satisfied because 0 + 2 >= 2).
        "history.cursor": {"data": [[0, 2, 2]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert len(rows) == 2
    assert rows[0]["_secid"] == "GAZP"


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
    assert outcome == "error"
    assert rows == []


def test_fetch_year_moex_outcome_malformed_missing_tradedate():
    """history.columns missing TRADEDATE -> 'malformed'."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["SECID", "BOARDID", "OPEN", "CLOSE"],
            "data": [["GAZP", "TQBR", 100, 101]],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
    assert rows == []


def test_fetch_year_moex_outcome_short_page_no_cursor_is_malformed():
    """No cursor + short page cannot certify completeness."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # No history.cursor; len(rows) < page_size (500).
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_partial_incomplete_cursor():
    """cursor offset + len < total -> 'partial'."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [["2025-09-29", "GAZP", "TQBR",
                      100, 102, 99, 101, 1000, 5, 100000]] * 100,
        },
        # offset 0, total 1000, page_size 100 -> incomplete.
        "history.cursor": {"data": [[0, 1000, 100]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "partial"
    assert len(rows) == 100


def test_fetch_year_moex_outcome_identity_mismatch_poisons_batch():
    """A single cross-listed mirror row poisons the whole fetch."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000, 5, 100000],
                # identity mismatch — single row, batch poisoned.
                ["2025-09-30", "SBER", "TQBR",
                 200, 202, 199, 201, 2000, 10, 200000],
            ],
        },
        "history.cursor": {"data": [[0, 2, 2]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "identity_mismatch"
    assert len(rows) == 2


def test_fetch_year_moex_outcome_worst_severity_wins():
    """First page error forces outcome == 'error' even if subsequent
    pages parse."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

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
                "data": [["2025-09-29", "GAZP", "TQBR"]],
            },
            "history.cursor": {"data": [[0, 1, 1]]},
        })

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=fake_get):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "error"
    assert rows == []


def test_fetch_year_moex_outcome_non_200_status_is_malformed():
    """HTTP 500 is malformed, not complete, even if body parses."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 500

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
    assert rows == []


def test_fetch_year_moex_outcome_initial_cursor_offset_mismatch_is_malformed():
    """Loop asks for start=0, server reports offset=2 -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # offset 2, total 3, page_size 1 -> loop asked start=0.
        "history.cursor": {"data": [[2, 3, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_repeated_cursor_offset_is_malformed():
    """Two pages with the same cursor offset -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    page = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID"],
            "data": [["2025-09-29", "GAZP", "TQBR"]],
        },
        # Same offset on every page — no progress, malformed.
        "history.cursor": {"data": [[0, 999, 1]]},
    }

    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(page)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_missing_ohlc_nonzero_volume_is_malformed():
    """OPEN/HIGH/LOW/CLOSE absent on a non-zero VOLUME row -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    # Columns list includes all OHLC + VOLUME, but the row is short
    # on the OHLC side (None) AND has a non-zero VOLUME.
    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 1000, 5, 100000],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_fetch_year_moex_outcome_zero_trade_missing_counters_is_malformed():
    """VOLUME=0 row without explicit NUMTRADES=0/VALUE=0 -> malformed."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                # NUMTRADES / VALUE missing (None) on a zero-volume
                # row — must be malformed, not complete.
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, None, None],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
```

Run `apps/api/tests/test_moex_no_trade_evidence.py` and confirm
11 RED failures (`AttributeError: module
'algotrader_api.ingestion.backfill' has no attribute
'_fetch_year_moex_outcome'`,
`ImportError: cannot import name 'MOEXFetchOutcome'`).

#### Step 2: RED — bar-list compatibility (legacy signature preserved)

Existing tests that use `backfill._fetch_year_moex` MUST keep
passing unchanged. The legacy signature is positional-or-keyword
(no `*` before `last_trading_day`); the wrapper preserves the
exact same shape. Add a regression test:

```python
def test_fetch_year_moex_list_only_signature_preserved_positional():
    """The existing list-only path keeps the
    positional-or-keyword ``last_trading_day`` signature; callers
    can pass it positionally or by keyword."""
    from algotrader_api.ingestion import backfill
    import datetime as _dt

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        # last_trading_day passed POSITIONALLY (legacy call style).
        rows = backfill._fetch_year_moex(
            "shares", "TQBR", "GAZP", 2025, _dt.date(2025, 9, 29),
        )
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_boardid"] == "TQBR"
```

Run that single test; expected PASS today (proves existing
behaviour is preserved) and that the implementation in Step 4
keeps it that way.

#### Step 3: RED — outcome emitted for the existing recent-tail shape

The bar consumer's existing `test_fetch_year_moex_emits_raw_columns_per_dict`
test must still pass after the outcome addition. Add:

```python
def test_fetch_year_moex_outcome_emits_raw_columns_per_dict():
    """Regression: the existing raw-columns test must still pass after
    the outcome addition; the outcome variant agrees on shape."""
    from algotrader_api.ingestion import backfill

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    payload = {
        "history": {
            "columns": ["TRADEDATE", "SECID", "BOARDID",
                        "OPEN", "HIGH", "LOW", "CLOSE",
                        "VOLUME", "NUMTRADES", "VALUE"],
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0, 0, 0],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    import unittest.mock as _mock
    with _mock.patch.object(backfill.requests, "get",
                            side_effect=lambda *a, **kw: _FakeResp(payload)):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_numtrades"] == 0
```

Run the existing `test_fetch_year_moex_emits_raw_columns_per_dict`
test in `apps/api/tests/test_moex_no_trade_evidence.py`; expected
PASS today. After Step 4 it MUST still PASS.

#### Step 4: GREEN — implement the outcome variant

In `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`,
add at module scope (above the existing helpers):

```python
from typing import Literal

# Fetch outcome emitted by ``_fetch_year_moex_outcome``. Decision
# rule (see ADDED Requirement in
# openspec/changes/persist-historical-moex-evidence): one value per
# fetch, worst-severity across pages. This is a ``Literal`` alias,
# NOT a runtime constructor — callers compare with
# ``outcome == "complete"`` and friends.
MOEXFetchOutcome = Literal[
    "complete", "partial", "error", "malformed", "identity_mismatch",
]
```

In `apps/api/src/algotrader_api/ingestion/backfill.py`:

1. Add the re-export at the top of the file (after the existing
   imports; do NOT redeclare — the alias lives in
   `no_trade_evidence`):
   ```python
from .no_trade_evidence import MOEXFetchOutcome  # noqa: F401
```
   (No circular import: `no_trade_evidence.py` does not import
   from `backfill.py` at module load time; it lazy-imports
   inside `_evidence_writer_lock`.)
2. Add a `_reduce_outcomes(prev, new) -> MOEXFetchOutcome`
   private helper with the severity ordering
   `error > malformed > partial > identity_mismatch > complete`
   (return the more severe of the two). Keep it private to the
   module.
3. Refactor the loop body of `_fetch_year_moex` into a private
   `_fetch_year_moex_iter(market, board, ticker, year, *,
   last_trading_day=None) -> Iterator[tuple[list[dict],
   MOEXFetchOutcome]]`. The legacy `_fetch_year_moex` becomes:
   ```python
def _fetch_year_moex(
    market: str,
    board: str,
    ticker: str,
    year: int,
    last_trading_day: date | None = None,
) -> list[dict]:
    """Bar-list wrapper. Signature preserved
    (positional-or-keyword ``last_trading_day``) so existing
    callers keep working."""
    rows: list[dict] = []
    for page_rows, _outcome in _fetch_year_moex_iter(
        market, board, ticker, year,
        last_trading_day=last_trading_day,
    ):
        rows.extend(page_rows)
    return rows
```
4. Add `_fetch_year_moex_outcome(market, board, ticker, year,
   last_trading_day=None) -> tuple[list[dict], MOEXFetchOutcome]`
   as a sibling that returns both. It walks the same iterator
   and collects all parsed rows plus the worst-severity outcome.
5. Inside `_fetch_year_moex_iter`:
   - On any HTTP / network exception in the `requests.get` call,
     yield `([], "error")` and stop.
   - On a response whose `status_code != 200`, yield
     `([], "malformed")` and stop.
   - On a response whose `history.columns` is empty OR does not
     contain `TRADEDATE` OR the four OHLC columns OR `VOLUME`,
     yield `([], "malformed")` and stop.
   - On the cursor:
     - If absent AND `len(rows) < page_size` (short first page),
       yield `([], "malformed")` and stop.
     - If absent AND `len(rows) >= page_size`, mark `partial`
       (cannot certify completeness without a cursor).
     - If present, parse `[offset, total, page_size]`. The
       server-reported `offset` MUST equal the loop's
       requested `start`; mismatch → `malformed` and stop.
       Non-positive `total` → `malformed` and stop. Repeated
       cursor offset across two pages → `malformed` and stop.
       If `offset + len(rows) < total`, mark `partial`.
   - For each parsed row:
     - `len(row) == len(cols)`; otherwise drop the row.
     - For zero-trade rows (`VOLUME == 0`): `NUMTRADES` and
       `VALUE` MUST be present and equal to `0`; otherwise
       drop the row and mark the page `malformed` if the
       counters are missing (or treat the row as malformed
       and stop the loop).
     - For non-zero-trade rows: every OHLC column MUST be
       present; missing OHLC on a non-zero row → drop the row
       and mark the page `malformed`.
     - Check `SECID == ticker` AND `BOARDID == board`. Track
       an `identity_mismatch` flag if any row fails the
       check.
   - Yield after each page. The caller picks the worst
     severity.

Preserve all existing per-dict fields (`figi`, `ts`, `open`,
`high`, `low`, `close`, `volume`, `source`, `_secid`,
`_boardid`, `_numtrades`, `_value`).

#### Step 5: GREEN — re-export the literal and verify

```bash
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_moex_recent_tail_fixes.py \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_backfill_metadata_poison.py -q
```

Expected: all green. The 11 new tests in Step 1 now PASS; the
two new tests in Steps 2 and 3 PASS; the existing
`test_fetch_year_moex_emits_raw_columns_per_dict` and the
identity-test file PASS unchanged.

#### Step 6: Verify and commit

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

Reviewer passes only if:
- The 11 outcome tests PASS (complete / partial / error /
  malformed / identity_mismatch / worst-severity / non-200 /
  cursor-offset-mismatch / repeated-cursor / missing-OHLC /
  zero-trade-missing-counters).
- The legacy positional-or-keyword signature test PASSES
  (preserved bar-list contract).
- The raw-columns regression test PASSES (existing bar
  consumer unchanged).
- The identity-test file PASSES unchanged.
- No `MOEXFetchOutcome("...")` constructor call exists in any
  test or production code — all comparisons are plain
  `outcome == "..."`.

---

### Task 2 (mandatory): Evidence helper, business-date filter, mandatory walker integration

**Files:**
- Modify: `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`
  (add `_is_business_date_for_evidence` and
  `record_historical_no_trade_evidence`; the helper signature
  carries the explicit `ticker` argument)
- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
  (bind `BackfillRunner._fetch_year_moex_outcome` to the
  instance; refactor `_process_moex_year` and `_process_one`
  historical walk branch to use the new outcome gate; the
  walker is statically bound to the new fetch symbol via
  `self._fetch_year_moex_outcome = _fetch_year_moex_outcome`
  in the runner's `__post_init__` / `__init__`)
- Modify: `apps/api/scripts/backfill_no_trade_evidence.py`
  (replace the `record_no_trade_evidence` call with
  `record_historical_no_trade_evidence`; no new argument
  surface, no new exit code path, no new branch)
- Modify: `apps/api/tests/test_moex_no_trade_evidence.py`
  (helper + business-date RED tests)
- Modify: `apps/api/tests/test_backfill_source_routing.py`
  (walker integration RED tests)

**Interfaces:**
- `apps.api.ingestion.no_trade_evidence.MOEXFetchOutcome` — from
  Task 1.
- `apps.api.ingestion.no_trade_evidence._is_business_date_for_evidence(
  conn, ts: str, *, today: date | None = None) -> bool` — new
  private helper. Returns `True` iff `ts` parses as an ISO date,
  is a weekday, and is not present in `moex_holidays` for the
  supplied `conn`. `today` is used only to clamp the holiday
  scan range.
- `apps.api.ingestion.no_trade_evidence.record_historical_no_trade_evidence(
  conn, *, db_path, figi, ticker, rows, board, isin, outcome,
  today=None) -> int` — new public wrapper.
  - `outcome` MUST be the `MOEXFetchOutcome` returned by
    `_fetch_year_moex_outcome` for the batch. Any value other
    than `"complete"` short-circuits: returns `0`, performs no
    SQLite mutation, and emits one structured log line.
  - When `outcome == "complete"`, the helper:
    1. Filters `rows` to those whose `_secid == ticker` (the
       explicit `ticker` argument — never guessed from
       `rows[0].get("_secid")`) AND `_boardid == board`.
    2. Filters `rows` to those whose `ts` parses as an ISO
       date AND is a business date per
       `_is_business_date_for_evidence`. Rows that fail this
       check are dropped from the persisted set but counted
       in `non_business_date` rejections.
    3. Delegates the actual `INSERT … ON CONFLICT` to the
       existing `record_no_trade_evidence` with the filtered
       rows. TTL semantics, recent-vs-historical expiry
       branching, real-bar-wins filtering, and ON CONFLICT
       refresh behaviour are preserved verbatim.
- `BackfillRunner._process_moex_year` and `_process_one`
  (historical walk branch) call `self._fetch_year_moex_outcome(...)`
  and route only `complete` outcomes to
  `record_historical_no_trade_evidence(...)` with the explicit
  `ticker` from the `instruments` row. The bar list passed to
  the downstream `replace_bars_for_figi` is unchanged.

#### Step 1: RED — outcome gating

In `apps/api/tests/test_moex_no_trade_evidence.py`, add:

```python
def test_record_historical_no_trade_evidence_rejects_partial(tmp_path):
    """Partial outcome short-circuits and writes nothing."""
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    rows = [
        {"ts": "2025-09-29", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU000GAZP",
        outcome="partial",
    )
    assert written == 0
    n = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n == 0


def test_record_historical_no_trade_evidence_rejects_error(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=[{"ts": "2025-09-29"}], board="TQBR", isin="RU",
        outcome="error",
    )
    assert written == 0


def test_record_historical_no_trade_evidence_rejects_malformed(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=[{"ts": "2025-09-29"}], board="TQBR", isin="RU",
        outcome="malformed",
    )
    assert written == 0


def test_record_historical_no_trade_evidence_rejects_identity_mismatch(tmp_path):
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    rows = [{"ts": "2025-09-29", "_secid": "SBER", "_boardid": "TQBR"}]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU",
        outcome="identity_mismatch",
    )
    assert written == 0
```

Run those four tests; expected RED with
`ImportError: cannot import name
'record_historical_no_trade_evidence'`.

#### Step 2: RED — business-date filter

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

#### Step 3: RED — complete outcome with business-date filter

In the same file, add:

```python
def test_record_historical_no_trade_evidence_complete_filters_non_business(
    tmp_path,
):
    """Complete outcome + mixed business / non-business rows -> only
    business-date rows are persisted."""
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
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
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU",
        outcome="complete",
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
        RECENT_EVIDENCE_EXPIRY,
        HISTORICAL_EVIDENCE_EXPIRY,
        record_historical_no_trade_evidence,
    )
    import datetime as _dt

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    today = _dt.date(2026, 9, 30)
    rows = [
        {"ts": "2026-09-25", "_secid": "GAZP", "_boardid": "TQBR"},
        {"ts": "2026-08-01", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU",
        outcome="complete", today=today,
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
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    _insert_bar(con, "FIGI1", "2025-09-30", close=100)
    rows = [
        {"ts": "2025-09-30", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU",
        outcome="complete",
    )
    assert written == 0
    n = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n == 0


def test_record_historical_no_trade_evidence_uses_explicit_ticker_not_rows_zero(
    tmp_path,
):
    """The helper MUST use the explicit ``ticker`` argument, not
    ``rows[0].get("_secid")``. A batch whose first row is a mirror
    SECID and whose every other row matches the caller's ticker is
    rejected — the helper is end-to-end, not zero-trust."""
    from algotrader_api.ingestion.no_trade_evidence import (
        record_historical_no_trade_evidence,
    )

    db = tmp_path / "nte.db"
    con = _make_conn(db)
    rows = [
        # First row is a mirror (would mislead a "guess from rows[0]"
        # implementation). Every other row is identity-correct.
        {"ts": "2025-09-29", "_secid": "SBER", "_boardid": "TQBR"},
        {"ts": "2025-09-30", "_secid": "GAZP", "_boardid": "TQBR"},
    ]
    written = record_historical_no_trade_evidence(
        con, db_path=str(db), figi="FIGI1", ticker="GAZP",
        rows=rows, board="TQBR", isin="RU",
        outcome="complete",
    )
    # The SBER row is dropped (it does not match the caller's ticker),
    # but the GAZP row is persisted.
    assert written == 1
    rows_db = con.execute(
        "SELECT session_date FROM moex_no_trade_evidence "
        "WHERE figi='FIGI1' ORDER BY session_date"
    ).fetchall()
    assert [r["session_date"] for r in rows_db] == ["2025-09-30"]
```

Run those four tests; expected RED with `ImportError`.

#### Step 4: GREEN — implement the helper

In `apps/api/src/algotrader_api/ingestion/no_trade_evidence.py`,
add the two new helpers ABOVE the existing
`record_no_trade_evidence`:

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
    row = conn.execute(
        "SELECT 1 FROM moex_holidays WHERE date = ?",
        (d.isoformat(),),
    ).fetchone()
    return row is None
```

Then add the public wrapper:

```python
def record_historical_no_trade_evidence(
    conn: sqlite3.Connection,
    *,
    db_path: str,
    figi: str,
    ticker: str,
    rows: list[dict],
    board: str,
    isin: str,
    outcome: MOEXFetchOutcome,
    today: date | None = None,
) -> int:
    """Persist zero-trade evidence for a historical MOEX walk.

    Outcome gate:
      * ``outcome == "complete"`` — proceed to the
        business-date / identity filter and delegate to
        :func:`record_no_trade_evidence`.
      * any other outcome — return ``0`` immediately, perform no
        SQLite mutation, and emit one structured log line.

    Filter rule (after outcome gate):
      * keep rows whose ``_secid`` matches the explicit ``ticker``
        argument (the caller threads the ticker through from the
        ``instruments`` row — the helper MUST NOT guess the
        ticker from ``rows[0].get("_secid")``);
      * keep rows whose ``_boardid`` matches ``board``;
      * keep rows whose ``ts`` is a valid ISO date AND a
        business date per :func:`_is_business_date_for_evidence`.

    Writer lock: acquired exactly once through
    :func:`_evidence_writer_lock` with
    ``role="no-trade-evidence"`` and ``phase="evidence"`` (the
    same role/phase pair the existing wrapper uses).

    Delegation: the actual ``INSERT ... ON CONFLICT`` is
    performed by the existing :func:`record_no_trade_evidence`
    so TTL, recent-vs-historical expiry branching,
    real-bar-wins filtering, and ON CONFLICT refresh behaviour
    stay verbatim.
    """
    if outcome != "complete":
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_rejected figi=%s outcome=%s rows=%s",
            figi, outcome, len(rows),
        )
        return 0
    if not rows:
        return 0
    accepted: list[dict] = []
    for r in rows:
        if str(r.get("_secid") or "") != ticker:
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
            figi, len(rows),
        )
        return 0
    return record_no_trade_evidence(
        conn,
        db_path=db_path,
        figi=figi,
        rows=accepted,
        board=board,
        isin=isin,
        now=today,
    )
```

Add `import logging` at the top of the file if not already
imported. Re-run the RED tests from Steps 1–3. Expected GREEN.

#### Step 5: RED/GREEN — bind the walker to the new fetch symbol

The historical walker (`BackfillRunner.backfill_from_moex` ->
inner `_process_moex_year` closure) MUST call the new
`_fetch_year_moex_outcome` and route only `complete` outcomes to
the evidence helper. Verified against the live code:

* `BackfillRunner._fetch_year_moex` is a class-bound
  `staticmethod` (line ~804 of `backfill.py`); mirror the same
  binding for the new variant so tests can monkeypatch at the
  class level via
  `BackfillRunner._fetch_year_moex_outcome = staticmethod(...)`.
* `_process_moex_year` is an inner closure of
  `backfill_from_moex` (not a method on the class) so its body
  is the only place to inject the outcome gate. The legacy
  `_fetch_year_moex` staticmethod stays untouched — bar
  consumers keep calling it.
* `self._conn` does NOT exist on the runner; the writer-lock
  helper takes the explicit `db_path=self.db_path` (the helper
  opens its own short-lived connection from the same db_path).
  The test fixtures confirm: `record_no_trade_evidence` accepts
  an injected `conn` and an explicit `db_path`.

Add to `BackfillRunner` alongside the existing class-bound
helpers (the `staticmethod(...)` line near the existing
`_fetch_year_moex = staticmethod(_fetch_year_moex)`):

```python
# Statically bound: the historical walk uses the outcome-emitting
# variant. Tests can monkeypatch
# ``BackfillRunner._fetch_year_moex_outcome`` to inject a strict
# synthetic feed; bar consumers that still call the list-only
# ``_fetch_year_moex`` are unchanged.
_fetch_year_moex_outcome = staticmethod(_fetch_year_moex_outcome)
```

In `backfill_from_moex._process_moex_year`, replace the call to
`self._fetch_year_moex(...)` with:

```python
year_bars, outcome = self._fetch_year_moex_outcome(
    meta["market"], meta["board"], inst["ticker"], year,
    last_trading_day=yesterday,
)
# Bar list is the rows the fetcher returned; consumer contract
# preserved. Identity filter stays (defensive second check; the
# fetcher already enforces per-batch identity, but the bar
# consumer additionally drops mirror rows before bar writes).
year_bars = _filter_moex_bars_by_identity(
    year_bars, ticker=inst["ticker"], board=meta["board"],
)
for b in year_bars:
    b["figi"] = inst["figi"]

# Evidence path: gate on outcome, thread the explicit ticker
# (never guess from rows[0]). Busy on the evidence lock MUST NOT
# undo the bar write; the bar list above is already filtered and
# will be passed to replace_bars_for_figi by the caller
# (asyncio.gather on _process_moex_year). The writer-lock helper
# opens its own short-lived connection from db_path, so a busy
# lock only blocks the evidence write, never the bar write.
if outcome == "complete" and year_bars:
    from ..db.bars_sqlite import get_connection
    try:
        record_historical_no_trade_evidence(
            get_connection(self.db_path),
            db_path=self.db_path,
            figi=inst["figi"], ticker=inst["ticker"],
            rows=year_bars, board=meta["board"],
            isin=str(inst.get("isin") or ""),
            outcome=outcome,
        )
    except Exception as _exc:  # noqa: BLE001
        # Busy on the evidence lock OR a transient failure MUST
        # NOT undo the bar write. The bars table commit is owned
        # by the caller; we only log here.
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_deferred figi=%s reason=%s",
            inst["figi"], type(_exc).__name__,
        )
```

(The `_process_one` historical walk branch is unchanged — it
already calls `_process_moex_year` per year, and the new
binding above is the only injection point. No duplicated HTTP
fetch; one `_fetch_year_moex_outcome` call per year per figi.)

#### Step 6: RED — walker integration tests

In `apps/api/tests/test_backfill_source_routing.py`, define
the test inline. The file currently exports `runner` (a
`BackfillRunner` over the migrated tmp DB) and `_run_migrations`
(autouse); the test must NOT invent cross-module fixtures —
define `_make_runner`, `await_runner`, and the outcome stubs
inline (no fixture import).

```python
import asyncio
import json
import sqlite3
from datetime import date
from unittest.mock import MagicMock

import pytest
import responses as _responses

from algotrader_api.ingestion.backfill import BackfillRunner


def _make_runner(db_path):
    """Inline test-local runner factory. NOT a fixture import."""
    return BackfillRunner(
        client=MagicMock(),
        db_path=db_path,
        event_sink=lambda ev: None,
        run_id=0,
    )


async def await_runner(runner, *, figi):
    """Run the historical walker for one figi on the tmp DB and
    return the total bars written. Drives the real public
    `backfill_from_moex`; monkeypatches the new outcome-binding
    at the class level so the test never reads production data
    and never calls the live broker."""
    # Pre-populate the per-ticker metadata so decide_strategy
    # does not fall through to the "no metadata -> full" branch.
    from algotrader_api.db import sqlite as _sqlitedb
    _sqlitedb.close_all()
    return await runner.backfill_from_moex(today=date(2024, 12, 31))


@_responses.activate
def test_walker_partial_outcome_writes_no_evidence_but_keeps_bars(
    tmp_path, monkeypatch,
):
    """Partial historical fetch leaves moex_no_trade_evidence
    untouched; the real bar (returned by the partial response)
    is still written to the bars table.

    The strict synthetic feed is a real `responses`-mocked HTTP
    payload; the bar consumer sees a real-looking 1-page complete
    row for 2024-01-15, but the page reports `total=2` while
    only 1 row is returned -> outcome 'partial'. The bar
    consumer writes the row it has; the evidence helper sees
    'partial' and short-circuits.
    """
    # 1. Set up an isolated tmp DB with the canonical schema.
    from algotrader_api.db import sqlite as _sqlitedb
    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    db_path = str(tmp_path / "state.db")
    _sqlitedb.run_migrations(db_path, migrations_dir)
    _sqlitedb.close_all()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size, isin) "
        "VALUES ('GAZP', 'BBG00RU000A1', 'share', 'Gazp', 'rub', 10, 'RU0007661625')"
    )
    con.commit()
    con.close()

    # 2. Stub the new outcome-emitting fetcher at the class
    #    level so `backfill_from_moex` routes through the new
    #    contract. Returns (rows, 'partial') for every (year, ...).
    from algotrader_api.ingestion import backfill as _backfill_mod
    real_partial_row = [{
        "figi": None, "ts": "2024-01-15", "open": 100, "high": 102,
        "low": 99, "close": 101, "volume": 1000, "source": "moex",
        "_secid": "GAZP", "_boardid": "TQBR",
        "_numtrades": 5, "_value": 100000,
    }]
    monkeypatch.setattr(
        _backfill_mod.BackfillRunner, "_fetch_year_moex_outcome",
        staticmethod(lambda *a, **kw: (real_partial_row, "partial")),
    )
    # Stub _get_meta_moex so decide_strategy finds a MOEX meta
    # row without doing a real HTTP probe.
    monkeypatch.setattr(
        _backfill_mod.BackfillRunner, "_get_meta_moex",
        staticmethod(lambda ticker, today, *, meta_cache, meta_lock: {
            "market": "shares", "board": "TQBR",
            "listed_from": "2014-01-01", "listed_till": today.isoformat(),
            "isin": "RU0007661625",
        }),
    )
    # Stub prefetch_moex_meta and the universe discovery so
    # backfill_from_moex does not try to call the broker.
    async def _no_prefetch(self, instruments): return None
    monkeypatch.setattr(
        _backfill_mod.BackfillRunner, "prefetch_moex_meta", _no_prefetch,
    )

    # 3. Drive the real public historical walker.
    runner = _make_runner(db_path)
    n = asyncio.run(await_runner(runner, figi="BBG00RU000A1"))

    # 4. Assertions: the partial bar was written; no evidence
    #    was recorded for the same date.
    con = sqlite3.connect(db_path)
    n_bars = con.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG00RU000A1' AND ts='2024-01-15'"
    ).fetchone()[0]
    n_evidence = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence "
        "WHERE figi='BBG00RU000A1'"
    ).fetchone()[0]
    con.close()
    assert n_bars == 1, f"expected 1 bar written, got {n_bars}"
    assert n_evidence == 0, (
        f"partial outcome must NOT record evidence, got {n_evidence}"
    )
```

Run that test; expected RED until Step 5 is implemented. After
Step 5: GREEN.

#### Step 7: GREEN — wire the historical CLI as a thin call-through

In `apps/api/scripts/backfill_no_trade_evidence.py`:

1. Import `record_historical_no_trade_evidence` and
   `_fetch_year_moex_outcome`.
2. Replace the existing per-figi block that calls
   `_fetch_moex_range` + `_extract_zero_trade_rows` with a
   block that:
   - Calls `_fetch_year_moex_outcome(market, board, ticker,
     year)` for each touched calendar year (preserving the
     existing `last_trading_day` cap).
   - Aggregates the rows across years and reduces the outcome
     to the worst severity across pages (use
     `backfill._reduce_outcomes`).
   - On non-`complete`, emits exactly one
     `moex_historical_evidence_rejected figi={figi} ...` line
     and continues to the next figi. No new exit code; the
     existing `return 75` on `WriterLockBusy` is unchanged.
   - On `complete`, delegates to
     `record_historical_no_trade_evidence(...)` with the
     explicit `db_path` AND the explicit `ticker` from the
     `instruments` row.

The CLI does NOT gain a new argument surface, a new exit
code, or a new branch. The `argparse` parser is unchanged.

#### Step 8: Verify and commit

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
git diff --check
git branch --show-current
env -u PYTHONPATH -u PYTHONHOME /home/hermes/algotrader/apps/api/.venv/bin/python -m pytest \
  apps/api/tests/test_moex_no_trade_evidence.py \
  apps/api/tests/test_backfill_no_trade_evidence_lock.py \
  apps/api/tests/test_moex_all_paths_identity.py \
  apps/api/tests/test_moex_recent_tail_fixes.py \
  apps/api/tests/test_backfill_source_routing.py \
  apps/api/tests/test_backfill_metadata_poison.py -q
git add apps/api/src/algotrader_api/ingestion/no_trade_evidence.py \
        apps/api/src/algotrader_api/ingestion/backfill.py \
        apps/api/scripts/backfill_no_trade_evidence.py \
        apps/api/tests/test_moex_no_trade_evidence.py \
        apps/api/tests/test_backfill_source_routing.py
git commit -m "feat(ingestion): gate historical zero-trade evidence on validated complete fetches"
```

Reviewer passes only if:
- The four outcome-rejection tests in Step 1 PASS (partial /
  error / malformed / identity_mismatch).
- The four business-date tests in Step 2 PASS.
- The four complete-outcome tests in Step 3 PASS (filter, TTL
  preservation, real-bar-wins, explicit-ticker-not-rows-zero).
- The walker integration test in Step 6 PASSes.
- The existing `record_no_trade_evidence` tests PASS
  unchanged (TTL semantics preserved).
- The existing `_fetch_year_moex` tests PASS unchanged
  (bar-list consumer compatibility preserved).

---

### Task 3 (mandatory, end-to-end): strict-synthetic-feed walker test

**Files:**
- Modify: `apps/api/tests/test_backfill_coverage.py` (new
  end-to-end test, function name
  `test_walker_end_to_end_gate_improves_on_temp_db`)

**Goal:** Prove the gate improves on a TEMP DB without
fabricating bars, without reading production, and without a
manual denominator override. The test exercises the
**real** public historical walker
`BackfillRunner.backfill_from_moex(today=date(2024, 12, 31))`
end-to-end; the test itself never opens a writer connection
or hand-builds evidence rows.

**Scenario (matches the ADDED Requirement in the spec):**
- Pin the clock to `today = date(2024, 12, 31)` for every
  module that calls `date.today()` (see `_pin_clock` below).
  Without this pin, `check_coverage`, the walker, and
  `populate_expected_bars` would compute `last_session` from
  the real wall clock, the cached `expected_bars` would not
  match the gate's window, and the test would be
  non-deterministic.
- Run real migrations on the TEMP DB (no cross-module
  fixture imports — call
  `algotrader_api.db.sqlite.run_migrations(db_path,
  migrations_dir)` inline).
- Seed one share figi (`BBG00RU000A1 / GAZP / TQBR`) with
  `source_updated_at = 2014-01-15` (the listing anchor) and
  one real-shape bar at the same date
  (`2024-01-15` is **NOT** the anchor — see the surrogate
  appendix; the real anchor is `2014-01-15`, the
  `source_updated_at` of the figi row, and the last completed
  business session is `2024-12-30`).
- Pre-populate `expected_bars` on the TEMP DB by running
  the actual `populate_expected_bars` CLI with
  `sys.argv = ['populate_expected_bars', '--db', TEMPDB,
  '--refresh']` BEFORE the walker runs. The cached value is
  the canonical denominator; without it, the pre-evidence
  `check_coverage` reports `expected=None` (`unknown_expected`
  reason) and a `1/None` comparison does NOT prove the ratio
  is below 0.95. The prepopulate pass makes the pre-assertion
  a real ratio comparison.
- Pre-evidence `check_coverage(conn, [figi])` MUST report
  the figi as failing with `reason: 'incomplete'` (NOT
  `unknown_expected`); `bars_count / expected` MUST be below
  0.95. This is the prepopulate guard: the cached denominator
  is real, and the bars are fresh (1 row in the window),
  so the gate is in the real-insufficient-coverage branch,
  not the unknown/stale branch.
- Stub `BackfillRunner._fetch_year_moex_outcome` at the
  class level (the future binding Step 5 of Task 2
  introduces) to return a paginated per-year strict synthetic
  feed: for every `(market, board, ticker, year)` call,
  return `(rows_for_year, "complete")`. The feed spans every
  calendar year from `2014` to `2024`; each year's rows
  include:
  - the listing-anchor real-shape OHLC bar at
    `2014-01-15` (the `source_updated_at`);
  - the last-session real-shape OHLC bar at
    `2024-12-30` (Monday; not in `moex_holidays`);
  - explicit zero-trade rows for every other weekday in
    `[2014-01-15, 2024-12-30]` that is NOT a
    `moex_holidays` entry (`open=high=low=close=None`,
    `volume=numtrades=value=0`, `_secid=GAZP`,
    `_boardid=TQBR`).
  All rows carry matching SECID/BOARDID so the new
  identity check in `_fetch_year_moex_outcome` passes;
  explicit zero counters on zero-trade rows so the row-shape
  contract holds.
- Stub `BackfillRunner._get_meta_moex` at the class level
  so `decide_strategy` does not do a real HTTP probe;
  identity MUST match the seeded `instruments` row
  (`market=shares, board=TQBR, listed_from=2014-01-15,
  listed_till=yesterday(today), isin=RU0007661625`).
- Run the public historical walker
  `BackfillRunner.backfill_from_moex(today=date(2024, 12, 31))`
  against the TEMP DB. The walker drives the real
  `record_historical_no_trade_evidence` (Task 2) per
  figi-year where outcome is `complete`; the test MUST NOT
  call the writer directly.
- Re-invoke the actual existing
  `populate_expected_bars` script (imported by path, NOT a
  new wrapper) with monkeypatched `sys.argv` =
  `['populate_expected_bars', '--db', str(TEMPDB),
  '--refresh']` so the canonical `expected_bars` is
  recomputed against the TEMP DB AFTER the walker writes
  evidence. Production threshold / universe / expected
  formula are unchanged.
- Open a fresh `sqlite3.Connection` with
  `row_factory = sqlite3.Row` and call
  `check_coverage(conn, [figi])` — the actual public
  signature is `(conn, figis, coverage_threshold=0.95) ->
  list[dict]` (empty list = all OK; non-empty = failing
  figis). Assert the returned list is empty.
- Assert `bars` contains exactly the anchor + last-session
  real bars (2 rows); zero-trade rows are dropped by
  `replace_bars_for_figi(..., replace=False)` because they
  violate `bars.NOT NULL` on `open/high/low/close` — no
  fabricated OHLC.
- Assert the pre-evidence ratio was below 0.95 (real
  ratio, not `1/None`) and the post-evidence ratio is at
  or above 0.95.

**Verified against the live code:**
- `check_coverage` signature (from
  `apps/api/src/algotrader_api/ml/features.py:46`):
  `(conn: sqlite3.Connection, figis: list[str],
   coverage_threshold: float = 0.95) -> list[dict[str, Any]]`.
  The plan MUST use this exact signature; no `dict[figi -> ...]`
  return shape, no `db_path` argument. The caller MUST set
  `conn.row_factory = sqlite3.Row` because the helper
  internally uses `row[3]` (positional) AND `row['listed_till']`
  on the optional `listed_till` column.
- `populate_expected_bars.main()` signature is `() -> int`; the
  script parses `sys.argv` directly via `argparse` with
  `--db <path>` (default
  `/home/hermes/algotrader/apps/api/data/state.db`) and
  `--refresh` flag. The plan MUST NOT add a new
  `peb.run(db_path)` wrapper; just import the module, monkey
  patch `sys.argv`, and call `peb.main()`. No production
  default DB path is reachable because the test passes an
  explicit `--db` that points at the TEMP DB. The script
  imports `from datetime import date` at module load time, so
  the `_PinnedDate` clock patch MUST be re-applied to the
  `populate_expected_bars` module's `date` attribute AFTER
  `spec.loader.exec_module(peb)` returns (the loader captures
  the real class into the module's globals at import time).
- `BackfillRunner._get_meta_moex` signature (live
  `apps/api/src/algotrader_api/ingestion/backfill.py:219`):
  `(ticker: str, yesterday: date, *, meta_cache: dict,
   meta_lock: threading.Lock) -> dict | None`. The stub
  MUST match the keyword-only `meta_cache` / `meta_lock`
  parameters; the existing production call site at
  `backfill.py:1149` uses exactly these kwargs.
- `BackfillRunner._fetch_year_moex` and
  `BackfillRunner._get_meta_moex` are class-level
  `staticmethod` bindings declared OUTSIDE `__post_init__`
  (live `backfill.py:804–806`). The new
  `_fetch_year_moex_outcome` (Task 1) and the
  `record_historical_no_trade_evidence` helper (Task 2) MUST
  be added with the same binding pattern; tests can then
  `monkeypatch.setattr(BackfillRunner, '_fetch_year_moex_outcome',
  staticmethod(...))` to inject the strict synthetic feed
  without HTTP.
- The walker does NOT call `prefetch_moex_meta` in the
  current code path (`backfill_from_moex` is the only entry
  point that touches it; the new test uses the historical
  walker which goes through `decide_strategy` →
  `_get_meta_moex`, not `prefetch_moex_meta`). The test
  MUST NOT add a `prefetch_moex_meta` stub — the round3
  surrogate appendix calls this out.
- `record_historical_no_trade_evidence` interface (Task 2):
  `(conn, *, db_path, figi, ticker, rows, board, isin,
  outcome, today=None) -> int`. The `ticker` is the
  explicit argument from the `instruments` row — the helper
  MUST NOT guess it from `rows[0].get("_secid")`. The test
  asserts evidence was persisted by inspecting the row
  count in `moex_no_trade_evidence` AFTER the walker
  returns; the test does NOT call
  `record_historical_no_trade_evidence` itself.
- `backfill_from_moex` signature (live
  `backfill.py:1058`): `(*, today: date | None = None,
  max_workers: int = 5, delta_only: bool = True,
  priority: bool = True, recent_tail_days: int = 0) ->
  int`. Returns total bars written across all figis. The
  test passes only `today=date(2024, 12, 31)` and ignores
  the return value; the assertion is on the table state.
- Test fixtures `_make_strict_synthetic_feed`,
  `_make_runner`, `_pin_clock`, `_PinnedDate`, `_holiday_set`,
  `_invoke_populate` do NOT exist in `test_backfill_coverage.py`
  today (only `_seed_instruments`, `_seed_migrations`,
  `_migrations_dir`). Define them concretely inline in the
  new test; do NOT import across test modules. The
  class-level `staticmethod` stub is simpler and tighter
  for a single figi on a TEMP DB than the `responses`-based
  HTTP-shape payload; both options are equivalent for the
  gate-improvement assertion, the class-level stub is
  preferred.

**Concrete test code (illustrative; do not lower validation to
fit a sloppy mock):**

```python
import asyncio
import importlib
import importlib.util
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from algotrader_api.ingestion.backfill import BackfillRunner
from algotrader_api.ml.features import check_coverage


FIGI = "BBG00RU000A1"
TICKER = "GAZP"
BOARD = "TQBR"
ISIN = "RU0007661625"
ANCHOR = date(2014, 1, 15)        # listing anchor (source_updated_at)
LAST_SESSION = date(2024, 12, 30)  # last completed business day
TODAY = date(2024, 12, 31)        # pinned clock


class _PinnedDate(date):
    """Frozen date subclass returned by ``date.today()`` so every
    module that calls ``date.today()`` agrees on the same
    value. See the round3 surrogate appendix for the
    force-import / re-pin dance for ``populate_expected_bars``.
    """
    _PINNED_TODAY = TODAY

    @classmethod
    def today(cls) -> "_PinnedDate":
        return cls.fromordinal(cls._PINNED_TODAY.toordinal())

    @classmethod
    def fromtimestamp(cls, t: float) -> "_PinnedDate":
        return cls.fromordinal(cls._PINNED_TODAY.toordinal())


_RealDate = date  # keep the real class for isinstance checks


def _pin_clock(today: date) -> None:
    """Replace ``date`` in every relevant module's namespace with
    :class:`_PinnedDate` so the helpers agree on the same
    ``today()`` return value. Force-imports each module first
    so the rebind reaches an existing ``date`` global.
    """
    _PinnedDate._PINNED_TODAY = today
    for modname in (
        "algotrader_api.ml.features",
        "algotrader_api.ml.coverage",
        "algotrader_api.ingestion.backfill",
        "algotrader_api.ingestion.no_trade_evidence",
    ):
        importlib.import_module(modname)
        mod = sys.modules.get(modname)
        if mod is not None and hasattr(mod, "date"):
            mod.date = _PinnedDate


def _seed_temp_db(tmp_path: Path) -> str:
    """Build a fully-migrated TEMP DB with one share figi and
    one real-shape bar at the listing anchor. The figi's
    ``source_updated_at`` is the anchor date; the bar is
    seeded at the SAME date. ``expected_bars`` is left NULL
    (canonical; populated later via the CLI).
    """
    from algotrader_api.db import sqlite as _sqlitedb
    migrations_dir = str(
        Path(__file__).resolve().parent.parent
        / "src/algotrader_api/db/migrations"
    )
    db_path = str(tmp_path / "gate.db")
    _sqlitedb.run_migrations(db_path, migrations_dir)
    _sqlitedb.close_all()
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments "
        "(ticker, figi, class, name, currency, lot_size, isin, "
        "source_updated_at) "
        "VALUES (?, ?, 'share', 'Gazp', 'rub', 10, ?, ?)",
        (TICKER, FIGI, ISIN, ANCHOR.isoformat()),
    )
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES (?, ?, 100, 102, 99, 101, 1000, 'tinkoff')",
        (FIGI, ANCHOR.isoformat()),
    )
    con.commit()
    con.close()
    return db_path


def _holiday_set(db_path: str) -> set[str]:
    """Return MOEX-holiday ISO date strings seeded by migration 006."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT date FROM moex_holidays").fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


def _make_strict_synthetic_feed_year(
    year: int,
    holidays: set[str],
) -> tuple[list[dict], str]:
    """Build the strict synthetic feed for ONE calendar year.

    Returns ``(rows, "complete")`` so the new
    ``_fetch_year_moex_outcome`` returns a valid tuple.
    The walker is bound (Task 2 Step 5) to call this per
    year; the new helper paginates the rows internally so a
    single tuple is enough for the test.

    * Anchor (2014-01-15) — real-shape OHLC.
    * Last session (2024-12-30) — real-shape OHLC.
    * Every other weekday in [year-01-01, year-12-31] that
      is NOT in ``holidays`` is an explicit zero-trade row
      (open=high=low=close=None, volume=numtrades=value=0,
      _secid=TICKER, _boardid=BOARD).
    """
    rows: list[dict] = []
    real_bar_dates = {ANCHOR, LAST_SESSION}
    cur = date(year, 1, 1)
    end = date(year, 12, 31)
    while cur <= end:
        iso = cur.isoformat()
        if cur in real_bar_dates:
            rows.append({
                "figi": None, "ts": iso,
                "open": 100, "high": 102, "low": 99, "close": 101,
                "volume": 1000, "source": "moex",
                "_secid": TICKER, "_boardid": BOARD,
                "_numtrades": 5, "_value": 100000,
            })
        elif cur.weekday() < 5 and iso not in holidays:
            rows.append({
                "figi": None, "ts": iso,
                "open": None, "high": None, "low": None, "close": None,
                "volume": 0, "source": "moex",
                "_secid": TICKER, "_boardid": BOARD,
                "_numtrades": 0, "_value": 0,
            })
        cur += timedelta(days=1)
    return rows, "complete"


def _make_strict_synthetic_feed(db_path: str) -> list[dict]:
    """Strict synthetic feed spanning every calendar year from
    the anchor to the last session. Paginated per year
    (the new fetcher consumes one year at a time, so the
    test returns a flat list and the stub slices by year
    inside the closure).
    """
    holidays = _holiday_set(db_path)
    out: list[dict] = []
    for year in range(ANCHOR.year, LAST_SESSION.year + 1):
        rows, _outcome = _make_strict_synthetic_feed_year(year, holidays)
        out.extend(rows)
    return out


def _paginated_stub(db_path: str):
    """Build a per-year class-level staticmethod stub. The
    walker calls
    ``self._fetch_year_moex_outcome(market, board, ticker,
    year, last_trading_day=...)`` once per (year, figi);
    this closure filters the flat synthetic feed down to the
    requested year and returns the strict tuple.
    """
    flat = _make_strict_synthetic_feed(db_path)
    by_year: dict[int, list[dict]] = {y: [] for y in range(ANCHOR.year, LAST_SESSION.year + 1)}
    for r in flat:
        y = int(r["ts"][:4])
        by_year[y].append(r)

    def _stub(*args, **kwargs) -> tuple[list[dict], str]:
        # Signature: (market, board, ticker, year, last_trading_day=None)
        year = args[3] if len(args) >= 4 else kwargs.get("year")
        return by_year.get(int(year), []), "complete"

    return staticmethod(_stub)


def _make_runner(db_path: str) -> BackfillRunner:
    return BackfillRunner(
        client=MagicMock(),
        db_path=db_path,
        event_sink=lambda ev: None,
        run_id=0,
    )


async def _await_runner(runner: BackfillRunner) -> int:
    """Run the public historical walker. The walker drives the
    real public ``backfill_from_moex`` which calls
    ``self._fetch_year_moex_outcome`` (Task 1) and
    ``record_historical_no_trade_evidence`` (Task 2) per
    figi-year. No ``prefetch_moex_meta`` stub — the walker
    does not call it on this path.
    """
    return await runner.backfill_from_moex(today=TODAY)


def _invoke_populate(db_path: str) -> int:
    """Invoke the actual existing ``populate_expected_bars``
    CLI with monkeypatched ``sys.argv``; NO new wrapper.
    The --refresh flag recomputes the canonical
    ``expected_bars`` for every figi in the universe. After
    exec_module, the loaded module's ``date`` is re-pointed
    at :class:`_PinnedDate` so the CLI's
    ``from datetime import date`` reference also sees the
    pinned clock.
    """
    spec = importlib.util.spec_from_file_location(
        "populate_expected_bars",
        Path(__file__).resolve().parent.parent
        / "scripts/populate_expected_bars.py",
    )
    peb = importlib.util.module_from_spec(spec)
    saved_argv = sys.argv[:]
    sys.argv[:] = [
        "populate_expected_bars",
        "--db", db_path,
        "--refresh",
    ]
    try:
        spec.loader.exec_module(peb)
        # Re-pin: the loader captured the real ``date`` into
        # the module's globals at import time.
        if hasattr(peb, "date"):
            peb.date = _PinnedDate
        return peb.main()
    finally:
        sys.argv[:] = saved_argv


def test_walker_end_to_end_gate_improves_on_temp_db(
    tmp_path, monkeypatch,
):
    """End-to-end: real public walker consumes a paginated
    strict synthetic feed, writes only explicit zero-trade
    evidence via the new helper, recomputes expected_bars
    on the TEMP DB via the existing CLI, and improves
    check_coverage from below 0.95 (real ratio) to at or
    above 0.95. No fabricated bars, no manual denominator
    override, no production read, no unpinned wallclock.
    """
    # Pin the clock BEFORE the first call to date.today() so
    # the cached last_session and the helper's last_session
    # agree.
    _pin_clock(TODAY)
    db_path = _seed_temp_db(tmp_path)

    # Pre-evidence step 1: prepopulate expected_bars via the
    # real CLI. Without this, the cached value is NULL and
    # the pre-assertion is `expected=None` (unknown_expected)
    # which does NOT prove the ratio is below 0.95.
    rc = _invoke_populate(db_path)
    assert rc == 0, f"pre-prepopulate populate_expected_bars exit={rc}"

    # Pre-evidence step 2: the cached denominator is now
    # real; the pre-assertion is a genuine ratio comparison.
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    pre = check_coverage(con, [FIGI])
    pre_cached = con.execute(
        "SELECT expected_bars FROM instruments WHERE figi=?", (FIGI,),
    ).fetchone()["expected_bars"]
    con.close()
    assert pre, f"pre-evidence check_coverage must report failures, got {pre!r}"
    pre_figi = pre[0]
    assert pre_figi["figi"] == FIGI
    assert pre_figi["reason"] == "incomplete", (
        f"pre-evidence reason must be 'incomplete' (cached "
        f"denominator is real), got {pre_figi['reason']!r}"
    )
    pre_expected = pre_figi["expected"]
    assert pre_expected is not None and pre_expected > 0, (
        f"cached expected must be positive after prepopulate, "
        f"got {pre_expected!r}"
    )
    assert pre_figi["bars_count"] == 1
    assert pre_figi["bars_count"] / pre_expected < 0.95, (
        f"pre-evidence ratio must be below 0.95, got "
        f"{pre_figi['bars_count']}/{pre_expected} "
        f"= {pre_figi['bars_count'] / pre_expected:.4f}"
    )

    # Stub the new outcome-emitting fetcher at the class
    # level (Task 1 binding OUTSIDE __post_init__). The
    # closure filters the flat synthetic feed to the
    # requested year and returns the strict tuple.
    from algotrader_api.ingestion import backfill as _backfill_mod
    monkeypatch.setattr(
        _backfill_mod.BackfillRunner, "_fetch_year_moex_outcome",
        _paginated_stub(db_path),
    )
    # Stub the meta probe at the class level (live
    # backfill.py:219) so decide_strategy does not HTTP-probe.
    # Signature: (ticker, yesterday, *, meta_cache, meta_lock).
    monkeypatch.setattr(
        _backfill_mod.BackfillRunner, "_get_meta_moex",
        staticmethod(
            lambda ticker, yesterday, *, meta_cache, meta_lock: {
                "market": "shares", "board": BOARD,
                "listed_from": ANCHOR.isoformat(),
                "listed_till": yesterday.isoformat(),
                "isin": ISIN,
            }
        ),
    )

    # Drive the real public historical walker. The runner
    # calls ``record_historical_no_trade_evidence`` for every
    # (year, figi) where outcome is "complete" (Task 2
    # binding). The test does NOT call the writer directly.
    runner = _make_runner(db_path)
    asyncio.run(_await_runner(runner))

    # The bar writer saw every parsed bar (anchor + last
    # session + zero-trade). INSERT OR IGNORE on PRIMARY KEY
    # (figi, ts) and the NOT NULL constraint on
    # open/high/low/close drop the zero-trade rows, so the
    # bars table holds exactly the 2 real-shape bars.
    con = sqlite3.connect(db_path)
    n_real_bars = con.execute(
        "SELECT COUNT(*) FROM bars WHERE figi=?", (FIGI,),
    ).fetchone()[0]
    n_evidence = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence "
        "WHERE figi=?", (FIGI,),
    ).fetchone()[0]
    con.close()
    assert n_real_bars == 2, (
        f"expected exactly 2 real bars (anchor + last "
        f"session, no fabricated OHLC), got {n_real_bars}"
    )
    assert n_evidence > 0, (
        "evidence helper should have recorded every "
        "in-window weekday, non-holiday, zero-trade date "
        f"via record_historical_no_trade_evidence (Task 2); "
        f"got {n_evidence}"
    )

    # Re-prepopulate expected_bars AFTER the walker. The
    # cached denominator now subtracts the confirmed
    # no-trade evidence in window, so the post-ratio meets
    # the 0.95 threshold.
    rc = _invoke_populate(db_path)
    assert rc == 0, f"post-walk populate_expected_bars exit={rc}"

    # Post-evidence: the real bars + every weekday's
    # evidence row satisfies the gate; check_coverage
    # returns an empty list (no failing figis).
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    post = check_coverage(con, [FIGI])
    post_expected = con.execute(
        "SELECT expected_bars FROM instruments WHERE figi=?", (FIGI,),
    ).fetchone()["expected_bars"]
    n_bars = con.execute(
        "SELECT COUNT(*) FROM bars WHERE figi=?", (FIGI,),
    ).fetchone()[0]
    con.close()
    assert post == [], f"post-evidence check_coverage must pass, got {post!r}"
    assert post_expected is not None and post_expected > 0, (
        f"cached expected must be positive after post-walk "
        f"prepopulate, got {post_expected!r}"
    )
    assert n_bars / post_expected >= 0.95, (
        f"post-evidence ratio must be at or above 0.95, got "
        f"{n_bars}/{post_expected} = {n_bars / post_expected:.4f}"
    )
```

Run the new test; expected RED until Task 1 + Task 2 land.
After both land: GREEN. The test is end-to-end: it drives
the real public walker and asserts on the canonical gate,
not on helper internals.

#### Verify and commit

```bash
cd /home/hermes/worktrees/algotrader-historical-moex-evidence
git diff --check
git branch --show-current
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
git add apps/api/tests/test_backfill_coverage.py
git commit -m "test(ingestion): end-to-end strict-synthetic-feed walker proves gate improvement"
```

Reviewer verifies:
- The end-to-end test PASSes.
- The pre-evidence ratio is below 0.95 and the post-evidence
  ratio is at or above 0.95.
- The test does NOT read production and does NOT call the
  live broker.
- The test does NOT manually assign a denominator — the
  canonical `expected_bars` is recomputed via
  `populate_expected_bars` on the TEMP DB.
- No claim is made that all 215 incomplete-history figis are
  recoverable or that the daily phase hang is caused by
  missing evidence; the test exercises a single TEMP-DB
  figi.

---

### Round3 docs-only fix: deterministic calendar/freshness surrogate (no production code change)

**Status (2026-10-03):** Tasks 1–3 of this plan are future
TDD-RED work (the new helpers — `MOEXFetchOutcome`,
`_fetch_year_moex_outcome`, `record_historical_no_trade_evidence`,
the new binding on `BackfillRunner.backfill_from_moex`, the
business-date filter — do NOT exist in the live codebase at
commit `3ad3977`). An integrated "new historical walker
end-to-end" test therefore cannot be authored or run today
without first landing Task 1 + Task 2. Per the brief, this
section does NOT claim green for the new walker.

What this section DOES establish: a deterministic
fixture-arithmetic surrogate that proves the **existing**
gate + **existing** writer + **existing** populate CLI improve
on a strict synthetic feed inside a TEMP DB. No fabricated
provider data, no production reads, no manual denominator
override, no new helpers, no new code paths.

**Probe path:**

`/home/hermes/.hermes/cache/scratch/round3_probe.py`

**Run command:**

```bash
cd /home/hermes/algotrader
apps/api/.venv/bin/python /home/hermes/.hermes/cache/scratch/round3_probe.py
```

**Fixture shape (clock pinned to `today = 2024-12-31`):**

* Listing anchor — real-shape OHLC bar at `2014-01-15` (the
  figi's `source_updated_at`).
* Latest completed business session — synthetic OHLC bar at
  `2024-12-30` (Monday; not in `moex_holidays`).
* Every other weekday in `[2014-01-15, 2024-12-30]` not in
  `moex_holidays` — explicit zero-trade row
  (`open=high=low=close=None`, `volume=numtrades=0`,
  `value=0.0`, `_secid=GAZP`, `_boardid=TQBR`).
* Walked against the real public
  `BackfillRunner.backfill_from_moex(today=date(2024, 12, 31))`
  with two class-level `staticmethod` stubs (per the live
  pattern at `apps/api/src/algotrader_api/ingestion/backfill.py:804`
  — bindings declared OUTSIDE `__post_init__`):
  - `BackfillRunner._fetch_year_moex` → returns the strict
    synthetic feed for every `(market, board, ticker, year)`
    call.
  - `BackfillRunner._get_meta_moex` → returns
    `{market: "shares", board: "TQBR", listed_from:
    "2014-01-15", listed_till: yesterday.isoformat(), isin:
    "RU0007661625"}`.

**Clock pin (the brief calls this out explicitly):** every
helper that calls `date.today()` is rebound to a
`_PinnedDate` subclass via module-level attribute replacement.
Modules covered: `algotrader_api.ml.features`,
`algotrader_api.ml.coverage`,
`algotrader_api.ingestion.backfill`,
`algotrader_api.ingestion.no_trade_evidence`, plus the
loaded `populate_expected_bars` module (its `date` is
re-pointed after `spec.loader.exec_module(peb)` because
`from datetime import date` captured the real class into
the module's globals at load time). The CLI receives
`sys.argv = ["populate_expected_bars", "--db", <TEMP>,
"--refresh"]` and `peb.main()` is called — no new wrapper.

**Strict feed contract enforced before the writer call:**
the existing helper
`apps.api.ingestion.no_trade_evidence._extract_zero_trade_rows`
accepts only the explicit zero-trade shape (all OHLC
`None`, all counters `0`, matching SECID + BOARDID). The
probe routes the feed through this helper — the writer
NEVER sees unfiltered weekday evidence. The anchor and the
last-session real-shape rows are correctly dropped from the
zero-row list (they do not match the strict shape).

**Verified numbers (recorded on 2026-10-03, captured from
the probe stdout):**

| Stage                        | Value                |
|------------------------------|----------------------|
| `PRE` bars in DB             | `1`                  |
| `PRE` failing list           | non-empty, `reason: 'both'` (stale + unknown_expected) |
| `PRE` ratio (window)         | `0.0004` (1 / 2810) — well below 0.95 |
| `FEED` rows                  | `2810`               |
| `AFTER WALK` bars            | `2` (anchor + last_session) |
| `AFTER WALK` evidence rows   | `0` (walker does not call the writer today) |
| `ZERO_ROWS` accepted by `_extract_zero_trade_rows` | `2808` (2810 − 2 real bars) |
| `EVIDENCE` written (return)  | `2808`               |
| `EVIDENCE` first row expires_at | `2025-12-31` = `today + HISTORICAL_EVIDENCE_EXPIRY` ✓ |
| `POPULATE` exit code         | `0`                  |
| `POPULATE` `end_date`        | `2024-12-30` (yesterday from pinned today) |
| `POST` bars in DB            | `2` (unchanged)      |
| `POST` cached `expected_bars` | `2` (`business_days(2014-01-15, 2024-12-30) − 2808 evidence`) |
| `POST` ratio                 | `1.0000` (2 / 2) — at or above 0.95 |
| `POST` failing list          | `[]` (gate passes)   |

**Pre-evidence gate is NOT a real ratio claim:**
`PRE bars=1 / expected_in_window=2810 = 0.0004` is the
probe's own in-window arithmetic (computed by
`_expected_business_days_window` against the TEMP DB
holidays set), NOT `check_coverage`'s view. With
`expected_bars` left NULL until the post-walk
`populate_expected_bars --refresh` pass, the canonical
gate reports `expected=None` and reason `unknown_expected`
on the pre side. A `1/None` comparison is not a valid
"below 0.95" claim. The probe therefore asserts
`failing != []` on the pre side, not a ratio bound; the
ratio bound is asserted post-walk against the cached
denominator. The Task 3 future test corrects this
limitation by running the populate CLI BEFORE the walker
so the pre-side ratio is real (see Task 3 prepopulate
guard and `reason: 'incomplete'` assertion).

**Probe `bars_written_total=22` is attempts, not persisted
rows (explicit warning):** the existing helper
`replace_bars_for_figi` does `executemany` per year; the
returned rowcount sums attempted-then-conflicted inserts
across 11 years (the 2014–2024 window). The explicit-zero
rows violate the `bars.NOT NULL` constraint on
`open/high/low/close`, so the surrounding transaction
rolls back; the writer's rowcount still counts the
attempted inserts. End state in `bars` is exactly 2 rows
(anchor + last_session). The probe's `AFTER WALK bars=2`
assertion is the persisted count; `bars_written_total=22`
is a return-value signal, not a row-count signal, and the
test does not equate the two.

**Real-bar-wins filter (explicit check):** the probe asserts
that no evidence row exists for `2024-12-30` (the
synthetic OHLC bar) — `record_no_trade_evidence` skips any
session whose date is already in `bars`. The 2 `bars` rows
(2014-01-15 + 2024-12-30) are unchanged between
`AFTER WALK` and `POST` — the writer does not touch the
`bars` table.

**TTL semantics (single-class surrogate, with BOTH-class
guard):** the surrogate above pins `today=2024-12-31`
and every session_date in the 2014–2024 window is outside
the 14-day recent cutoff EXCEPT the very last one
(`2024-12-30` is `today - 1d`, in the recent branch).
The probe asserts the first row's `expires_at` equals
`pinned_today + HISTORICAL_EVIDENCE_EXPIRY` (2025-12-31);
that covers the historical class. To cover BOTH expiry
classes, an explicit guard probe runs with
`today=2026-09-30`:

|| Stage                          | Value             |
||--------------------------------|-------------------|
|| `GUARD` pinned today            | `2026-09-30`     |
|| `GUARD` recent_cutoff           | `2026-09-16`     |
|| `GUARD` recent row date         | `2026-09-25` (5d) — recent class |
|| `GUARD` recent row expires_at   | `2026-10-07` = `today + RECENT_EVIDENCE_EXPIRY` (7d) ✓ |
|| `GUARD` historical row date     | `2026-08-01` (60d) — historical class |
|| `GUARD` historical row expires_at | `2799/9` (2027-09-30) = `today + HISTORICAL_EVIDENCE_EXPIRY` (365d) ✓ |
|| `GUARD` row counts              | `n_recent=1, n_historical=1, n_total=2` (mixed-class proof) |

The guard probe does NOT depend on the surrogate above
and CAN be executed as a separate `pytest` test against
the existing writer (the same
`record_no_trade_evidence` codepath; the test merely
shifts `now` and the two row dates so each falls in a
different expiry branch). The guard probe is the
canonical proof that the existing writer's TTL branching
survives the helper unwrap; the surrogate probe is the
canonical proof that the calendar/freshness contract
survives the helper unwrap. Two probes, two
assertions, no false equivalence.

The future Task 2 test
`test_record_historical_no_trade_evidence_complete_keeps_ttl_semantics`
exercises the same BOTH-class pattern with the explicit
helper; that test will RED until Task 2 lands.

**Freshness contract (explicit check):** `populate_expected_bars`
runs `--refresh` against the TEMP DB, computes the canonical
`expected_bars` (= `business_days(listing, yesterday) −
confirmed zero-trade evidence in window`), and the result
matches the post-ratio the gate computes against the cached
column. The cached denominator is positive (`2`).

**What this probe does NOT prove:**

* The new `MOEXFetchOutcome` reduction rules (Tasks 1 + 2).
* The new business-date filter in the new helper
  `record_historical_no_trade_evidence` (Task 2, not yet
  merged; the probe uses the existing
  `record_no_trade_evidence` and applies the filter inline
  via `_extract_zero_trade_rows`).
* The new walker binding of `_fetch_year_moex_outcome`
  (Task 2, not yet merged; the probe stubs the existing
  `_fetch_year_moex` at the class level).
* The historical CLI rewrite to delegate to the new
  `_fetch_year_moex_outcome` + `record_historical_no_trade_evidence`
  chain (Task 2 Step 7, not yet merged).
* Any claim that all 215 incomplete-history figis are
  recoverable; the daily phase hang; or any production-side
  behaviour change.

These are intentionally separate TDD-RED workstreams that
land when Tasks 1 and 2 land. The probe above is a
fixture-arithmetic proof on the existing helpers, not a
proof of the new walker.

**Minor cleanups applied to the touched code path
(per brief):**

* The plan's original Task 3 contained a dead
  `prefetch_moex_meta = _no_prefetch` patch
  (`backfill_from_moex` does not call `prefetch_moex_meta`).
  The probe does not include that patch.
* The plan's original `_await_runner` accepted a `figi`
  kwarg the real `backfill_from_moex` does not consume.
  The probe's `_walk` passes only `today=date(2024,12,31)`.
* The new helpers will be `staticmethod` bindings declared
  on `BackfillRunner` OUTSIDE `__post_init__`, matching the
  live pattern at
  `apps/api/src/algotrader_api/ingestion/backfill.py:804`
  (`_fetch_year_moex = staticmethod(_fetch_year_moex)`,
  etc.), so tests can `patch.object(BackfillRunner,
  '_fetch_year_moex_outcome')` the same way. This is a
  Task 1 implementation note, recorded here for the reviewer.

---

### End-to-end smoke (after Tasks 1–3 land)

Run the focused regression set and the broader safe regression
set, identical to the standing convention:

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
(commit `3ad3977`). Bar counts in the existing partial-bar
tests remain identical. No production code touches the
production DB; no claims about the 215 incomplete-history
figis; no claim about the daily phase hang.
