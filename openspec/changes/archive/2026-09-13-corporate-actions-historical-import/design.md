# Design: corporate-actions-historical-import

## Stack

- **Python 3.11** stdlib only for the new code (`sqlite3`, `json`,
  `urllib.request`, `dataclasses`, `datetime`).
- **`tinkoff.invest`** for the dividends fetcher — already installed
  (algotrader sandbox uses it for bars already); no new dependency.
- **`pytest` + `unittest.mock`** for tests (already in use).

## Layout

```
algotrader/
├── apps/
│   └── api/
│       ├── scripts/
│       │   ├── data/
│       │   │   └── splits_curated.json                 (NEW)
│       │   ├── import_splits_curated.py                (NEW)
│       │   ├── import_corporate_actions_tinkoff.py    (NEW)
│       │   └── import_corporate_actions_moex.py       (NEW)
│       ├── src/algotrader_api/
│       │   └── scripts_import/
│       │       ├── import_corporate_actions_common.py (NEW)
│       │       ├── import_splits_curated.py            (NEW)
│       │       ├── import_corporate_actions_tinkoff.py (NEW)
│       │       └── import_corporate_actions_moex.py    (NEW)
│       └── tests/
│           ├── test_corporate_actions_common.py      (NEW)
│           ├── test_splits_curated.py                 (NEW)
│           ├── test_corporate_actions_tinkoff.py      (NEW)
│           └── test_corporate_actions_moex.py         (NEW)
└── openspec/
    └── specs/
        └── data-fetch/
            └── spec.md                                (MODIFY: +1 Requirement)
```

## Components

### 1. Common writer (`import_corporate_actions_common.py`)

```python
"""Shared writer + dataclass for corporate_actions importers."""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

ALLOWED_ACTION_TYPES = ("split", "dividend")


class InvalidActionType(ValueError):
    pass


@dataclass(frozen=True)
class CorporateActionRow:
    figi: str
    action_type: str
    ex_date: date
    factor: float
    cash_amount: Optional[float]
    note: str = ""

    def __post_init__(self):
        if self.action_type not in ALLOWED_ACTION_TYPES:
            raise InvalidActionType(...)
        if self.factor <= 0:
            raise ValueError("factor must be > 0")


def merge_into_corporate_actions(
    db_path: str, rows: Iterable[CorporateActionRow]
) -> int:
    """INSERT OR REPLACE. Returns count of input rows."""
    rows = list(rows)
    if not rows:
        return 0
    con = sqlite3.connect(db_path)
    try:
        con.executemany(
            """
            INSERT OR REPLACE INTO corporate_actions
                (figi, action_type, ex_date, factor, cash_amount, note)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [(r.figi, r.action_type, r.ex_date.isoformat(),
              r.factor, r.cash_amount, r.note) for r in rows],
        )
        con.commit()
    finally:
        con.close()
    return len(rows)
```

### 2. Curated splits loader (`import_splits_curated.py`)

```python
"""Load the bundled historical splits curated JSON.

This is the bootstrap: Tinkoff SDK has no native splits feed, and
MOEX ISS only provides current snapshots. We ship a one-shot
curated list of ~17 known splits (Russian paper + global samples).
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

from .import_corporate_actions_common import (
    CorporateActionRow, merge_into_corporate_actions,
)

CURATED_FILE = Path(__file__).parent / "data" / "splits_curated.json"


def load_curated_splits() -> list[CorporateActionRow]:
    raw = json.loads(CURATED_FILE.read_text())
    return [
        CorporateActionRow(
            figi=e["figi"],
            action_type="split",
            ex_date=date.fromisoformat(e["ex_date"]),
            factor=float(e["factor"]),
            cash_amount=None,
            note=e.get("note", ""),
        )
        for e in raw
    ]


def import_curated_splits(db_path: str) -> int:
    return merge_into_corporate_actions(db_path, load_curated_splits())


if __name__ == "__main__":
    import sys
    n = import_curated_splits(sys.argv[1])
    print(f"Imported {n} curated splits")
```

### 3. Tinkoff dividends fetcher (`import_corporate_actions_tinkoff.py`)

```python
"""Pull historical dividends from the Tinkoff Invest SDK.

Iterates every tradeable figi in `instruments` and calls
`client.instruments.get_dividends(figi, from_, to)`.
"""
from __future__ import annotations
import sqlite3
from datetime import date

from .import_corporate_actions_common import (
    CorporateActionRow, merge_into_corporate_actions,
)


def _event_date(ev) -> date:
    return date(ev.last_buy_date.year, ev.last_buy_date.month,
                ev.last_buy_date.day)


def _quotation_to_float(q) -> float:
    return float(q.units) + float(q.nano) / 1e9


def fetch_dividends_for_figi(client, figi: str,
                              from_: date, to: date
                              ) -> list[CorporateActionRow]:
    resp = client.instruments.get_dividends(figi=figi, from_=from_, to=to)
    out = []
    for ev in getattr(resp, "events", []):
        ex = _event_date(ev)
        if not (from_ <= ex <= to):
            continue
        cash = _quotation_to_float(ev.dividend_net)
        out.append(CorporateActionRow(
            figi=figi, action_type="dividend",
            ex_date=ex, factor=1.0, cash_amount=cash,
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
    db_path, client=None,
    from_=date(2015, 1, 1), to=date.today(),
) -> int:
    if client is None:
        from tinkoff.invest import Client
        from algotrader_api.config import Settings
        token = Settings().tinkoff_token
        client = Client(token)
    rows = []
    for figi in _tradeable_figis(db_path):
        rows.extend(fetch_dividends_for_figi(client, figi, from_, to))
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys
    n = import_corporate_actions_tinkoff(sys.argv[1])
    print(f"Imported {n} Tinkoff dividends")
```

### 4. MOEX ISS dividends fetcher (`import_corporate_actions_moex.py`)

```python
"""Pull historical dividends from the MOEX ISS REST API (free, no auth).

Endpoint: GET /iss/securities/{secid}/dividends.json

`figi → secid` mapping: `instruments.ticker` for MOEX paper.
US/global paper has no secid → silently skipped.
"""
from __future__ import annotations
import json
import sqlite3
import urllib.request
from datetime import date
from typing import Optional

from .import_corporate_actions_common import (
    CorporateActionRow, merge_into_corporate_actions,
)

ISS_BASE = "https://iss.moex.com/iss/securities"
UA = {"User-Agent": "algotrader-corporate-actions-importer/1.0"}


def _secid_from_figi(db_path, figi) -> Optional[str]:
    con = sqlite3.connect(db_path)
    cur = con.execute("SELECT ticker FROM instruments WHERE figi = ?",
                      (figi,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_dividends_for_secid(secid: str, from_: date, to: date
                              ) -> list[CorporateActionRow]:
    url = (f"{ISS_BASE}/{secid}/dividends.json"
           f"?from={from_.isoformat()}&till={to.isoformat()}")
    try:
        payload = _http_get_json(url)
    except Exception:
        return []  # network errors are not fatal
    block = payload.get("dividends", {})
    cols = block.get("columns", [])
    rows_data = block.get("data", [])
    if not cols or not rows_data:
        return []
    idx = {name: i for i, name in enumerate(cols)}
    date_idx = idx.get("registry_close_date")
    value_idx = idx.get("value")
    secid_idx = idx.get("secid")
    if date_idx is None or value_idx is None:
        return []
    out = []
    for row in rows_data:
        try:
            d = date.fromisoformat(row[date_idx])
            v = float(row[value_idx])
        except (ValueError, TypeError, IndexError):
            continue
        if v <= 0:
            continue
        if not (from_ <= d <= to):
            continue
        out.append(CorporateActionRow(
            figi=row[secid_idx] if secid_idx is not None else secid,
            action_type="dividend",
            ex_date=d, factor=1.0, cash_amount=v,
            note="moex:iss",
        ))
    return out


def import_corporate_actions_moex(db_path,
                                   from_=date(2015, 1, 1),
                                   to=date.today()) -> int:
    rows = []
    con = sqlite3.connect(db_path)
    cur = con.execute(
        "SELECT figi FROM instruments WHERE class IN ('share','etf','bond')"
    )
    figis = [r[0] for r in cur]
    con.close()
    for figi in figis:
        secid = _secid_from_figi(db_path, figi)
        if not secid:
            continue
        rows.extend(fetch_dividends_for_secid(secid, from_, to))
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys
    n = import_corporate_actions_moex(sys.argv[1])
    print(f"Imported {n} MOEX ISS dividends")
```

## Data flow

```
operator → python -m scripts.import_splits_curated state.db
   → import_splits_curated.load_curated_splits()
   → list[CorporateActionRow] (17 rows)
   → merge_into_corporate_actions() → 17 INSERT OR REPLACE → corporate_actions

operator → python -m scripts.import_corporate_actions_tinkoff state.db
   → for figi in instruments (3775 figis):
       client.instruments.get_dividends(figi, ...)
       → list[CorporateActionRow]
   → merge_into_corporate_actions() → N INSERT OR REPLACE → corporate_actions

operator → python -m scripts.import_corporate_actions_moex state.db
   → for figi in instruments (3775 figis):
       secid = instruments.ticker[figi]
       GET iss.moex.com/iss/securities/{secid}/dividends.json
       → list[CorporateActionRow]
   → merge_into_corporate_actions() → M INSERT OR REPLACE → corporate_actions

data-quality guardian (separate, unchanged) reads bars_adjusted:
   SELECT close / COALESCE(EXP(SUM(LN(factor))),1) AS adj_close
   FROM bars b
   JOIN corporate_actions ca ON ...
```

## Tests

- `test_corporate_actions_common.py` — writer: insert, idempotency,
  conflict update, invalid action_type rejected.
- `test_splits_curated.py` — file exists, ≥15 rows, all BBG-prefixed
  figis, all action_type='split', factor≥2.
- `test_corporate_actions_tinkoff.py` — mocked Tinkoff client returns
  events → rows are constructed correctly with cash_amount and currency
  note; empty response → empty rows; iterates every tradeable figi.
- `test_corporate_actions_moex.py` — mocked HTTP returns ISS payload →
  rows are parsed; skips zero-value rows; `figi → secid` resolves via
  `instruments.ticker`.

## Rollback

- Drop the new scripts (`rm -rf`).
- `corporate_actions` table itself stays — its data is harmless
  without the scripts.
- `bars_adjusted` view continues to return `adj_close = close` if no
  matching rows exist.
- Reverse: re-run the operator scripts to repopulate.

## Operator notes

- The three scripts are independent. Run any subset, in any order.
- Re-running is safe: every script uses `INSERT OR REPLACE` on the
  primary key.
- The curated splits JSON covers only events known at write-time; for
  splits after 2026-09-13 the curated approach is stale. The plan
  recommends a follow-up change to detect future splits via
  `face_value`/`lot` diffing, but that's out of scope here.
- The MOEX ISS importer runs at ~50 req/min rate limit; expect
  10-30 min for the full 3775-figi universe.
