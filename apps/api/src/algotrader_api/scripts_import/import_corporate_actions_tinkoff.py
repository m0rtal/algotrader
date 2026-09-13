# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py
"""Import historical dividends from the Tinkoff Invest SDK.

Iterates every tradeable figi in the `instruments` table and calls
`client.instruments.get_dividends(figi, from_, to)`. Each event becomes
a CorporateActionRow(action_type='dividend') and is merged via the
common writer.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from .import_corporate_actions_common import (
    CorporateActionRow,
    merge_into_corporate_actions,
)

# Tinkoff currency strings → USD conversion ratio for cross-currency
# dividends. Today we only have RUB dividends in the universe, so the
# default is identity; the function is here so we can plug a real
# conversion table later.
_CURRENCY_TO_RUB = {"rub": 1.0, "rub_": 1.0, "usd": None, "eur": None}


def _event_date(ev) -> date:
    return date(ev.last_buy_date.year, ev.last_buy_date.month, ev.last_buy_date.day)


def _quotation_to_float(q) -> float:
    return float(q.units) + float(q.nano) / 1e9


def fetch_dividends_for_figi(
    client, figi: str, from_: date, to: date,
) -> list[CorporateActionRow]:
    """Pull all dividend events for one figi from Tinkoff and convert to
    CorporateActionRow instances (action_type='dividend')."""
    resp = client.instruments.get_dividends(figi=figi, from_=from_, to=to)
    out: list[CorporateActionRow] = []
    for ev in getattr(resp, "events", []):
        ex = _event_date(ev)
        if not (from_ <= ex <= to):
            continue
        cash = _quotation_to_float(ev.dividend_net)
        out.append(CorporateActionRow(
            figi=figi,
            action_type="dividend",
            ex_date=ex,
            factor=1.0,
            cash_amount=cash,
            note=f"tinkoff: {ev.currency}",
        ))
    return out


def _tradeable_figis(db_path: str) -> list[str]:
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "SELECT figi FROM instruments WHERE class IN ('share','etf','bond')"
    )
    figis = [r[0] for r in cur]
    con.close()
    return figis


def import_corporate_actions_tinkoff(
    db_path: str,
    client=None,
    from_: date = date(2015, 1, 1),
    to: date = date.today(),
) -> int:
    """Iterate every tradeable figi, fetch dividends from Tinkoff,
    merge into corporate_actions.

    If `client` is None, opens a real `t_tech.invest.Client` using the
    production token from the algotrader secrets table. Tests pass a
    MagicMock.
    """
    if client is None:
        from t_tech.invest import Client  # pragma: no cover — live broker path
        from ..config import Settings  # pragma: no cover
        from ..db.secrets import get_broker_token  # pragma: no cover
        token = get_broker_token(Settings().sqlite_path) or ""  # pragma: no cover
        client = Client(token)  # pragma: no cover
    rows: list[CorporateActionRow] = []
    for figi in _tradeable_figis(db_path):
        rows.extend(fetch_dividends_for_figi(client, figi, from_, to))
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys  # pragma: no cover — operator entry point
    n = import_corporate_actions_tinkoff(sys.argv[1])
    print(f"Imported {n} Tinkoff dividends")