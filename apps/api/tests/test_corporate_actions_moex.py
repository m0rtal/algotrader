"""Tests for the MOEX ISS dividends fetcher (Task 4).

The MOEX ISS endpoint
    GET https://iss.moex.com/iss/securities/{secid}/dividends.json
is free, no auth. We mock `urllib.request.urlopen` with a `MagicMock`
whose `.read()` returns JSON bytes; `__enter__` / `__exit__` are
required because `urlopen` is used as a context manager.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


# --- fixtures & helpers ----------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Migrated DB on a fresh temp path."""
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    yield p


def _seed_figi(db_path, figi, ticker):
    import sqlite3

    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (figi, ticker, "share", ticker, "rub", 10),
    )
    con.commit()
    con.close()


def _mock_urlopen(payload):
    """Build a context-manager-friendly urlopen MagicMock."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None
    return resp


# --- tests -----------------------------------------------------------------


def test_secid_from_figi_via_instruments(db):
    """`_secid_from_figi` reads instruments.ticker (the secid proxy)."""
    _seed_figi(db, "BBG004730N88", "SBER")
    from algotrader_api.scripts_import.import_corporate_actions_moex import (
        _secid_from_figi,
    )

    assert _secid_from_figi(db, "BBG004730N88") == "SBER"


def test_secid_from_figi_returns_none_for_missing(db):
    from algotrader_api.scripts_import.import_corporate_actions_moex import (
        _secid_from_figi,
    )

    assert _secid_from_figi(db, "BBG000000000") is None


def test_fetch_dividends_parses_iss_payload():
    payload = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [
                ["SBER", "2024-07-08", 387.0, "RUB"],
                ["SBER", "2025-06-15", 250.0, "RUB"],
            ],
        }
    }
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen(payload)
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            secid="SBER", from_=date(2024, 1, 1), to=date(2025, 12, 31)
        )
    assert len(rows) == 2
    assert rows[0].cash_amount == 387.0
    assert rows[0].action_type == "dividend"
    # figi is the secid (caller maps back via instruments table)
    assert rows[0].figi == "SBER"
    # Source identifies the ingestion path that produced the row.
    assert rows[0].source == "moex:iss:dividends"
    assert rows[1].source == "moex:iss:dividends"


def test_fetch_dividends_skips_zero_value():
    payload = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [
                ["SBER", "2024-07-08", 0.0, "RUB"],
            ],
        }
    }
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen(payload)
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert rows == []


def test_fetch_dividends_skips_rows_outside_window():
    payload = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [
                ["SBER", "2023-07-08", 100.0, "RUB"],  # outside window
                ["SBER", "2024-07-08", 387.0, "RUB"],
            ],
        }
    }
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen(payload)
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert len(rows) == 1
    assert rows[0].cash_amount == 387.0


def test_fetch_dividends_handles_network_error_gracefully():
    """A network error returns [] — never raises."""
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.side_effect = OSError("network down")
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert rows == []


def test_fetch_dividends_handles_empty_payload():
    """An empty dividends block returns []."""
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen({"dividends": {"columns": [], "data": []}})
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert rows == []


def test_fetch_dividends_handles_missing_required_columns():
    """Payload missing `registry_close_date` / `value` returns []."""
    payload = {
        "dividends": {
            "columns": ["secid"],  # only secid; required cols absent
            "data": [["SBER"]],
        }
    }
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen(payload)
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert rows == []


def test_fetch_dividends_skips_malformed_row():
    """A row with a non-numeric value is skipped; other valid rows kept."""
    payload = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [
                ["SBER", "not-a-date", 387.0, "RUB"],  # bad date → skipped
                ["SBER", "2024-07-08", 387.0, "RUB"],  # good
                ["SBER", "2024-07-09", None, "RUB"],  # bad value (None) → skipped
            ],
        }
    }
    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.return_value = _mock_urlopen(payload)
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            fetch_dividends_for_secid,
        )

        rows = fetch_dividends_for_secid(
            "SBER", date(2024, 1, 1), date(2024, 12, 31)
        )
    assert len(rows) == 1
    assert rows[0].ex_date == date(2024, 7, 8)


def test_import_corporate_actions_moex_iterates_all_tradeable(db):
    """High-level: iterate every tradeable figi, resolve → secid,
    fetch dividends, merge into corporate_actions."""
    _seed_figi(db, "BBG004730N88", "SBER")
    _seed_figi(db, "BBG004730RP0", "GAZP")

    payload_sber = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [["SBER", "2024-07-08", 387.0, "RUB"]],
        }
    }
    payload_gazp = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [["GAZP", "2024-07-30", 80.0, "RUB"]],
        }
    }

    def _fake_urlopen(req, timeout=15):
        # The MOEX URL contains the secid; pick the right payload.
        url = req if isinstance(req, str) else req.full_url
        if "SBER" in url:
            return _mock_urlopen(payload_sber)
        if "GAZP" in url:
            return _mock_urlopen(payload_gazp)
        return _mock_urlopen({"dividends": {"columns": [], "data": []}})

    with patch(
        "algotrader_api.scripts_import.import_corporate_actions_moex.urllib.request.urlopen"
    ) as urlopen:
        urlopen.side_effect = _fake_urlopen
        from algotrader_api.scripts_import.import_corporate_actions_moex import (
            import_corporate_actions_moex,
        )

        n = import_corporate_actions_moex(
            db, from_=date(2024, 1, 1), to=date(2024, 12, 31)
        )
    assert n == 2

    import sqlite3

    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT figi, cash_amount FROM corporate_actions ORDER BY figi"
    ).fetchall()
    assert rows == [("GAZP", 80.0), ("SBER", 387.0)]