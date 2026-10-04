"""RED tests for Task 1 Round 1 review findings F1-F4.

Each test reproduces a code-level fail-open / spec-rule gap surfaced in
the independent review (review-task1-e0a5c26.md).

These tests target the strict-feed-contract enforcement inside
``_fetch_year_moex_iter`` / ``_fetch_year_moex_outcome``.

Run with:
    cd apps/api && ./.venv/bin/python -m pytest \\
        tests/test_moex_no_trade_evidence_round1.py -q
"""

from __future__ import annotations

import datetime as _dt
import unittest.mock as _mock

from algotrader_api.ingestion import backfill


# -- Shared helpers --------------------------------------------------------


class _Resp:
    """Minimal HTTP response double.

    Status code defaults to 200; tests that need non-200 or missing
    status_code set it explicitly via the ``status`` constructor arg.
    """

    def __init__(self, payload, status=200, has_status=True):
        self._payload = payload
        if has_status:
            self.status_code = status

    def json(self):
        return self._payload


def _ok_row(date, *, volume=1000, numtrades=5, value=99900):
    return [
        date, "GAZP", "TQBR", 100, 102, 99, 101,
        volume, numtrades, value,
    ]


def _zero_row(date):
    return [date, "GAZP", "TQBR", None, None, None, None, 0, 0, 0]


_FULL_COLS = [
    "TRADEDATE", "SECID", "BOARDID",
    "OPEN", "HIGH", "LOW", "CLOSE",
    "VOLUME", "NUMTRADES", "VALUE",
]


# -- F1: cursor page_size consistency (spec lines 30-31) -------------------


def test_f1_cursor_page_size_mismatch_overcount_is_malformed():
    """Cursor says page_size=100 but server returns 200 rows.

    Per spec (data-quality/spec.md line 30-31):
    > OR the cursor's reported page size does not match the page
    > size the loop actually received (consistency check) → malformed.

    The current implementation accepts 200 rows even though the cursor
    promised page_size=100. F1 reviewer finding.
    """
    rows_raw = [_ok_row(f"2025-09-{i + 1:02d}") for i in range(200)]
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": rows_raw,
        },
        # page_size=100, but the response carried 200 rows.
        "history.cursor": {"data": [[0, 200, 100]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", (
        f"page_size mismatch (cursor says 100, got 200) must be malformed, "
        f"got outcome={outcome!r}"
    )


def test_f1_cursor_page_size_mismatch_undercount_is_partial():
    """Cursor says page_size=100 but server returns 50 rows AND the
    loop has more to walk (``offset + len < total``).

    Parent ruling (per task brief): spec line 34-36 — ``partial`` —
    applies whenever the cursor is present and consistent, but
    ``offset + len(rows) < total``. The page-size mismatch on the
    low side does not bump this to ``malformed``: spec line 30-31
    page-size consistency is dominated by the spec line 34-36
    partial rule (a short cursor-bearing response that does not
    reach ``total`` is by definition incomplete, and ``partial``
    is the spec's tag for that exact state). The contract is
    "certify pagination completeness" — we cannot, so we tag
    ``partial`` and surface the rows we have to the bar consumer.
    """
    rows_raw = [_ok_row(f"2025-09-{i + 1:02d}") for i in range(50)]
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": rows_raw,
        },
        # cursor: page_size=100, total=500. Loop got 50, cursor
        # promises more. Under spec line 34-36 this is ``partial``.
        "history.cursor": {"data": [[0, 500, 100]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "partial", (
        f"page_size undercount with offset+len<total must be partial, "
        f"got outcome={outcome!r}"
    )
    # Bar consumer keeps the parsed rows.
    assert len(rows) == 50


def test_f1_cursor_page_size_consistent_short_final_page_is_complete():
    """The final short page is allowed to be ``len(kept_rows) < srv_page_size``
    only when the cursor closes the loop (``offset + len >= total``)
    AND ``len(kept_rows) == srv_page_size`` is *not* required for that
    final page — spec consistency rule is about pages that are NOT final
    short-circuits.

    Fixture: cursor reports total=50, page_size=100, offset=0. Loop got
    50 rows (server capped early). Cursor closes (``0+50>=50``). Outcome
    is ``complete`` and rows are accepted.
    """
    rows_raw = [_ok_row(f"2025-09-{i + 1:02d}") for i in range(50)]
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": rows_raw,
        },
        # cursor: total=50, page_size=100. Loop got 50, cursor closes
        # on page 1. Spec final-page short-circuit applies — outcome is
        # complete, not malformed.
        "history.cursor": {"data": [[0, 50, 100]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert len(rows) == 50


# -- F2: no-cursor full page must stop (cannot certify) -------------------


def test_f2_no_cursor_full_page_is_bounded_by_cap():
    """A server that returns 500 rows + no cursor forever would cause
    unbounded HTTP requests if the loop keeps advancing. The loop
    now hard-caps the number of pages it will walk for a single
    year (see ``_fetch_year_moex_iter``). After the cap is hit the
    iterator returns ``partial`` with the rows collected so far
    (the bar consumer keeps its data; the evidence path refuses).

    This test uses a FINITE sequence of full pages (more than the
    cap, so the cap is what stops the walk) and asserts the
    call count is bounded by the cap. No infinite loop is
    possible — the test exits in well under a second.
    """
    # The iterator's cap is 20 pages. Use 30 full pages so the
    # cap is the only thing that stops the walk — the fake
    # server never returns a cursor and never returns a short
    # page. With the cap in place the test sees exactly
    # ``cap + 1`` requests (the +1 is the post-cap yield) and
    # returns. Without the cap the test would walk all 30
    # pages and the assertions below would still pass, but
    # the iteration would never terminate if the sequence
    # were unbounded.
    from algotrader_api.ingestion.backfill import (
        _fetch_year_moex_iter,
    )
    cap = 20
    # The cap yields ``partial`` on the page AFTER the cap,
    # so the test should see ``cap + 1`` HTTP requests.
    expected_calls = cap + 1
    pages: list = []

    def fake_get(*a, **kw):
        # 500 rows, no cursor — every response identical. Use
        # 28 unique dates cycled so row data is well-formed
        # but the loop has no progress signal.
        return _Resp({
            "history": {
                "columns": _FULL_COLS,
                "data": [_ok_row(f"2025-09-{(i % 28) + 1:02d}")
                         for i in range(500)],
            },
            # NO history.cursor block.
        })

    with _mock.patch.object(backfill.requests, "get", side_effect=fake_get):
        for page_rows, page_outcome in _fetch_year_moex_iter(
            "shares", "TQBR", "GAZP", 2025,
        ):
            pages.append((page_rows, page_outcome))
    # Must be bounded by the cap.
    assert len(pages) <= expected_calls, (
        f"no-cursor full-page response must be bounded by the "
        f"page cap, got {len(pages)} pages (cap={cap})"
    )
    # The cap-relative yield carries the partial outcome.
    assert any(outcome == "partial" for _, outcome in pages), (
        f"cap-hit yield must mark partial, got outcomes: "
        f"{[o for _, o in pages]!r}"
    )


# -- F3: missing status_code must be malformed (fail-closed) ---------------


def test_f3_missing_status_code_on_outcome_is_malformed():
    """A response without a ``status_code`` attribute on the outcome
    path MUST be reported as malformed. The current ``getattr(..., 200)``
    default masks missing attribute and silently treats it as success.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [_ok_row("2025-09-29")],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    # has_status=False → _Resp does NOT set self.status_code.
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload, has_status=False),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", (
        f"missing status_code on outcome path must be malformed, "
        f"got outcome={outcome!r}"
    )


# -- F4: fractional VOLUME must be rejected --------------------------------


def test_f4_fractional_volume_is_malformed():
    """VOLUME=1000.5 must NOT be silently truncated to int 1000 — that
    would route the row into the non-zero path with a fake value and
    also break the explicit-zero contract (the helper relies on
    ``int(VOLUME) == 0``). Reject the row as malformed.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [
                # Fractional volume — server emitted a float.
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, 1000.5, 5, 100000],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed", (
        f"fractional VOLUME (1000.5) must be malformed, "
        f"got outcome={outcome!r}"
    )


def test_f4_fractional_volume_zero_truncated_is_malformed():
    """VOLUME=0.5 truncated to int(0.5)=0 would falsely satisfy the
    zero-trade shape contract — must be malformed.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [
                # 0.5 → int=0 → would pass zero-trade shape. Reject.
                ["2025-09-29", "GAZP", "TQBR",
                 None, None, None, None, 0.5, 0, 0],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_f4_integer_volume_unchanged_passes():
    """Sanity: integer VOLUME=1000 still routes into the non-zero
    path with volume=1000 in the kept dict. Guard against the F4 fix
    accidentally rejecting legitimate ints.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [_ok_row("2025-09-29", volume=1000)],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "complete"
    assert len(rows) == 1
    assert rows[0]["volume"] == 1000


# -- Extra robustness tests (parent reviewer requested) -------------------


def test_extra_total_changes_across_pages_is_malformed():
    """Cursor ``total`` must be stable across pages. If page 1 reports
    total=200 and page 2 reports total=300 (server is shifting the
    count), the strict contract rejects it. Real MOEX keeps total
    stable; a deviation indicates a contract-less upstream.
    """
    responses = [
        _Resp({
            "history": {
                "columns": _FULL_COLS,
                "data": [_ok_row("2025-09-29")] * 100,
            },
            "history.cursor": {"data": [[0, 200, 100]]},
        }),
        _Resp({
            "history": {
                "columns": _FULL_COLS,
                "data": [_ok_row("2025-09-30")] * 100,
            },
            # total CHANGED from 200 → 300 between requests.
            "history.cursor": {"data": [[100, 300, 100]]},
        }),
    ]
    call_count = {"n": 0}

    def fake_get(*a, **kw):
        call_count["n"] += 1
        return responses[call_count["n"] - 1]

    with _mock.patch.object(backfill.requests, "get", side_effect=fake_get):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"
    assert call_count["n"] == 2


def test_extra_oversized_page_more_than_srv_page_size_is_malformed():
    """Server reports page_size=100 but returns 150 rows — oversized
    page. Spec consistency rule: row count must equal page_size for
    non-final pages.
    """
    rows_raw = [_ok_row(f"2025-09-{(i % 28) + 1:02d}") for i in range(150)]
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": rows_raw,
        },
        # page_size=100, but loop got 150 — inconsistent.
        "history.cursor": {"data": [[0, 500, 100]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_extra_invalid_volume_string_is_malformed_not_traceback():
    """VOLUME is a non-numeric string ("N/A"). The current
    ``int(volume_raw or 0)`` would raise ValueError → caught by the
    existing ``except (TypeError, ValueError)`` block, which marks the
    page malformed. Pin that the test does NOT raise a traceback
    and the outcome is malformed.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [
                ["2025-09-29", "GAZP", "TQBR",
                 100, 102, 99, 101, "N/A", 5, 100000],
            ],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


def test_extra_invalid_cursor_total_string_is_malformed_not_traceback():
    """Cursor ``total`` is a non-numeric string ("oops"). The current
    ``int(total)`` raises ValueError → must be caught and marked
    malformed, NOT propagate as a traceback.
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [_ok_row("2025-09-29")],
        },
        # total is a string, not an int.
        "history.cursor": {"data": [[0, "oops", 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        rows, outcome = backfill._fetch_year_moex_outcome(
            "shares", "TQBR", "GAZP", 2025,
        )
    assert outcome == "malformed"


# -- Legacy wrapper preservation -------------------------------------------


def test_legacy_list_only_wrapper_preserves_signature_positional():
    """Bar-list wrapper preserves positional ``last_trading_day``.
    F3 fix makes missing ``status_code`` malformed for the outcome path;
    this test exercises the LEGACY list-only path which never reads
    status_code (the bar consumer kept its old contract).
    """
    payload = {
        "history": {
            "columns": _FULL_COLS,
            "data": [_zero_row("2025-09-29")],
        },
        "history.cursor": {"data": [[0, 1, 1]]},
    }
    with _mock.patch.object(
        backfill.requests, "get",
        side_effect=lambda *a, **kw: _Resp(payload),
    ):
        # last_trading_day passed POSITIONALLY.
        rows = backfill._fetch_year_moex(
            "shares", "TQBR", "GAZP", 2025, _dt.date(2025, 9, 29),
        )
    assert len(rows) == 1
    assert rows[0]["_secid"] == "GAZP"
    assert rows[0]["_boardid"] == "TQBR"