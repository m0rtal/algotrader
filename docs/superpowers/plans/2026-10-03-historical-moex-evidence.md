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

The historical walker MUST be statically bound to the new
fetch symbol. In `BackfillRunner.__post_init__` (or `__init__`
if no `__post_init__` exists), add:

```python
# Statically bound: the historical walk uses the outcome-emitting
# variant. Tests can monkeypatch
# ``runner._fetch_year_moex_outcome`` to inject a strict synthetic
# feed; bar consumers that still call the list-only
# ``_fetch_year_moex`` are unchanged.
self._fetch_year_moex_outcome = _fetch_year_moex_outcome
```

In `BackfillRunner._process_moex_year`, replace the call to
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
# (never guess from rows[0]).
if outcome == "complete" and year_bars:
    try:
        record_historical_no_trade_evidence(
            self._conn, db_path=self.db_path,
            figi=inst["figi"], ticker=inst["ticker"],
            rows=year_bars, board=meta["board"],
            isin=str(inst.get("isin") or ""),
            outcome=outcome,
        )
    except WriterLockBusy:
        # Busy on the evidence lock MUST NOT undo the bar write;
        # the bars table commit happened above. Log and continue.
        logging.getLogger("algotrader.ingestion").info(
            "moex_historical_evidence_deferred figi=%s reason=writer_lock_busy",
            inst["figi"],
        )
```

(For `_process_one` historical walk branch the same
substitution applies: route the outcome through the helper, the
bar list still goes to `replace_bars_for_figi`.)

#### Step 6: RED — walker integration tests

In `apps/api/tests/test_backfill_source_routing.py`, add a
fixture that binds the runner to a stubbed outcome and asserts:

```python
def test_walker_partial_outcome_writes_no_evidence_but_keeps_bars(
    fresh_db, monkeypatch,
):
    """Partial historical fetch leaves moex_no_trade_evidence
    untouched; the real bar (returned by the partial response)
    is still written to the bars table."""
    from algotrader_api.ingestion import backfill as backfill_mod
    from algotrader_api.ingestion import no_trade_evidence as nte

    db_path = fresh_db
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('BBG00RU000A1', '2024-01-15', 100, 102, 99, 101, 1000, 'tinkoff')"
    )
    con.commit()
    con.close()

    partial_bars = [
        {"ts": "2024-01-15", "open": 100, "high": 102, "low": 99,
         "close": 101, "volume": 1000, "source": "moex",
         "_secid": "GAZP", "_boardid": "TQBR",
         "_numtrades": 5, "_value": 100000},
    ]
    monkeypatch.setattr(
        backfill_mod.BackfillRunner, "_fetch_year_moex_outcome",
        lambda self, *a, **kw: (partial_bars, "partial"),
    )
    # The walker integration is a static class binding; a plain
    # list monkeypatch would NOT exercise the new path. The test
    # asserts the outcome-bearing path is the one the runner
    # actually uses.
    runner = _make_runner(db_path)
    n = await_runner(runner, figi="BBG00RU000A1")
    assert n == 1
    con = sqlite3.connect(db_path)
    n_evidence = con.execute(
        "SELECT COUNT(*) FROM moex_no_trade_evidence"
    ).fetchone()[0]
    assert n_evidence == 0
    n_bars = con.execute(
        "SELECT COUNT(*) FROM bars WHERE figi='BBG00RU000A1'"
    ).fetchone()[0]
    assert n_bars == 1
    con.close()
```

(Use the same `_make_runner` / `await_runner` / `fresh_db`
fixtures the file already defines; do not lower validation to
fit a sloppy mock.)

Run that test; expected RED until Step 5 is implemented.

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
  end-to-end test)

**Goal:** Prove the gate improves on a TEMP DB without
fabricating bars, without reading production, and without a
manual denominator override.

**Scenario (matches the ADDED Requirement in the spec):**
- Seed a TEMP DB with the project's migrations.
- Seed an instrument with
  `instruments.expected_bars` left NULL (canonical).
- Insert one real bar at `2024-01-15` (so the pre-evidence
  ratio is well below 0.95).
- Stub `runner._fetch_year_moex_outcome` to return a strict
  synthetic feed: one page, `start=0`/`offset=0`/`total=N`/
  `page_size=N`, `status_code = 200`, every row has matching
  SECID/BOARDID, every row is either the `2024-01-15` real
  bar (with full OHLC) or an explicit zero-trade shape on a
  weekday not in `moex_holidays`.
- Run the historical walker
  (`BackfillRunner._process_moex_year` + the in-process call
  path) for that figi.
- Run `populate_expected_bars` against the TEMP DB to
  recompute the canonical `expected_bars` (production
  threshold / universe / expected formula are unchanged).
- Call `check_coverage([figi])` and assert `ok=True`.
- Assert `bars` contains exactly one row (no fabricated OHLC).
- Assert the pre-evidence ratio was below 0.95 and the
  post-evidence ratio is at or above 0.95.

**Concrete test code (illustrative; do not lower validation to
fit a sloppy mock):**

```python
async def test_walker_end_to_end_gate_improves_on_temp_db(
    fresh_db, monkeypatch,
):
    """End-to-end: real public walker consumes a strict synthetic
    feed, writes only explicit zero-trade evidence, recomputes
    expected_bars on the TEMP DB, and improves check_coverage
    from below 0.95 to at or above 0.95. No fabricated bars,
    no manual denominator override, no production read."""
    from algotrader_api.ingestion import backfill as backfill_mod
    from algotrader_api.ingestion import no_trade_evidence as nte
    from algotrader_api.ml.features import check_coverage
    from algotrader_api.scripts import populate_expected_bars as peb

    db_path = fresh_db
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume, source) "
        "VALUES ('BBG00RU000A1', '2024-01-15', 100, 102, 99, 101, 1000, 'tinkoff')"
    )
    con.commit()
    con.close()

    # Pre-evidence: ratio is 1 / 252 ~ 0.4%, well below 0.95.
    pre = check_coverage(db_path, ["BBG00RU000A1"])
    assert pre["BBG00RU000A1"]["ok"] is False

    # Strict synthetic feed: one page, complete, full row
    # lengths, explicit zero counters on zero-trade rows,
    # matching SECID/BOARDID, valid ISO dates inside the window.
    rows = _make_strict_synthetic_feed(
        figi="BBG00RU000A1",
        ticker="GAZP", board="TQBR",
        start="2024-01-01", end="2024-12-31",
    )
    monkeypatch.setattr(
        backfill_mod.BackfillRunner, "_fetch_year_moex_outcome",
        lambda self, *a, **kw: (rows, "complete"),
    )

    runner = _make_runner(db_path)
    n_bars_written = await_runner(runner, figi="BBG00RU000A1")
    assert n_bars_written == 1  # only the real 2024-01-15 bar.

    # Recompute expected_bars on the TEMP DB (canonical).
    peb.run(db_path)
    con = sqlite3.connect(db_path)
    expected = con.execute(
        "SELECT expected_bars FROM instruments WHERE figi='BBG00RU000A1'"
    ).fetchone()[0]
    con.close()

    # Post-evidence: ratio improves to >= 0.95 (real bar +
    # explicit zero-trade evidence on every business day in
    # the window).
    post = check_coverage(db_path, ["BBG00RU000A1"])
    assert post["BBG00RU000A1"]["ok"] is True
    assert post["BBG00RU000A1"]["bars_count"] / expected >= 0.95
```

(The fixtures `_make_strict_synthetic_feed`, `_make_runner`,
`await_runner`, and `fresh_db` are defined alongside the
existing test file; do not lower validation to fit a sloppy
mock — the strict feed contract from Task 1 is the contract
under test.)

Run the new test; expected RED until Task 1 + Task 2 land.
After both land: GREEN.

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
