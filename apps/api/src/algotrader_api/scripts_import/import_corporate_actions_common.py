"""Common corporate-actions infrastructure: dataclass + DB writer.

Used by the historical-splits derivation importer and the dividends fetcher.
Intentionally minimal — no curated data, no JSON bootstrapping,
no Tinkoff/MOEX importers.

If you need more (splits from MOEX ISS), see
``scripts_import/import_corporate_actions.py`` for the original full
writer (deprecated for split detection).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


@dataclass(frozen=True)
class CorporateActionRow:
    """A single corporate-action row, ready to be inserted."""

    figi: str
    action_type: str           # 'split' | 'dividend' | ...
    ex_date: object            # datetime.date or 'YYYY-MM-DD' str
    factor: float              # split factor (1.0 for dividends)
    cash_amount: float         # dividend amount in currency (0.0 for splits)
    note: str = ""
    source: str = ""           # provenance tag — MUST be non-empty for derived rows


def merge_into_corporate_actions(
    db_path: str, rows: list[CorporateActionRow]
) -> int:
    """Insert rows, replacing any existing row with the same
    (figi, action_type, ex_date) PK. Returns number of rows actually
    written."""
    if not rows:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        written = 0
        for r in rows:
            ex_date = r.ex_date.isoformat() if hasattr(r.ex_date, "isoformat") else str(r.ex_date)
            cur = conn.execute(
                "SELECT 1 FROM corporate_actions "
                "WHERE figi=? AND action_type=? AND ex_date=?",
                (r.figi, r.action_type, ex_date),
            )
            if cur.fetchone() is not None:
                continue  # idempotent — skip duplicates
            conn.execute(
                "INSERT INTO corporate_actions "
                "(figi, action_type, ex_date, factor, cash_amount, note, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    r.figi,
                    r.action_type,
                    ex_date,
                    r.factor,
                    r.cash_amount,
                    r.note,
                    r.source,
                ),
            )
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Dividends
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DividendRow:
    """One dividend event as returned by a primary source.

    `revision_n` defaults to 1; the fetcher must increment it only when
    the broker / source explicitly indicates a retrospective correction.
    """

    figi: str
    ex_date: str                       # 'YYYY-MM-DD'
    period_year: int
    pay_date: str | None = None
    record_date: str | None = None
    declared_at: str | None = None
    period_no: int = 1
    currency: str = "rub"
    amount_per_share: float = 0.0
    fx_rate_used: float | None = None
    dividend_type: str = "regular"
    regularity: str | None = None
    close_price: float | None = None
    yield_value: float | None = None
    yield_pct: float | None = None
    tax_withheld_pct: float | None = None
    cancelled_at: str | None = None
    source: str = "tinkoff"
    source_revision_ts: str | None = None
    retrieved_at: str = ""
    revision_n: int = 1
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.figi:
            raise ValueError("figi is required")
        if not self.ex_date:
            raise ValueError("ex_date is required")
        if not self.source:
            raise ValueError("source is required")
        if not self.retrieved_at:
            msk = timezone(timedelta(hours=3))
            object.__setattr__(
                self, "retrieved_at",
                datetime.now(msk).strftime("%Y-%m-%dT%H:%M:%S"),
            )


def merge_into_dividends(db_path: str, rows: list[DividendRow]) -> int:
    """Insert rows into dividends using INSERT OR IGNORE on the PK.
    Returns the number of rows actually written. Idempotent."""
    if not rows:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        written = 0
        for r in rows:
            cur = conn.execute(
                "SELECT 1 FROM dividends WHERE "
                "figi=? AND ex_date=? AND period_year=? AND period_no=? AND revision_n=?",
                (r.figi, r.ex_date, r.period_year, r.period_no, r.revision_n),
            )
            if cur.fetchone() is not None:
                continue
            conn.execute(
                "INSERT INTO dividends ("
                "  figi, ex_date, pay_date, record_date, declared_at,"
                "  period_year, period_no, currency, amount_per_share,"
                "  fx_rate_used, dividend_type, regularity, close_price,"
                "  yield_value, yield_pct, tax_withheld_pct, cancelled_at,"
                "  source, source_revision_ts, retrieved_at, revision_n, note"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?, ?, ?, ?, ?)",
                (r.figi, r.ex_date, r.pay_date, r.record_date, r.declared_at,
                 r.period_year, r.period_no, r.currency, r.amount_per_share,
                 r.fx_rate_used, r.dividend_type, r.regularity, r.close_price,
                 r.yield_value, r.yield_pct, r.tax_withheld_pct, r.cancelled_at,
                 r.source, r.source_revision_ts, r.retrieved_at, r.revision_n,
                 r.note),
            )
            written += 1
        conn.commit()
        return written
    finally:
        conn.close()
