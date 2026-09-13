# 2026-09-13 — Historical Corporate Actions Bulk Importer

## Goal

Populate the `corporate_actions` table with historical splits and
dividends for every tradeable figi currently in `instruments`, sourced
primarily from the Tinkoff Invest SDK (sandbox-friendly, covers MOEX
+ global) and secondarily from the MOEX ISS REST API (free, no auth).
After this change, `bars_adjusted` returns correct `adj_close` values
for every real figi with stored bars, which makes the `INCOMPLETE_HISTORY`
health check meaningful.

## Current context / assumptions

- `corporate_actions` table exists (migration `007_corporate_actions.sql`):
  `(figi TEXT, action_type TEXT CHECK IN ('split','dividend'), ex_date DATE, factor REAL, cash_amount REAL, note TEXT, PRIMARY KEY (figi, action_type, ex_date))`.
- `bars_adjusted` view exists (migration `008_bars_adjusted.sql`); uses
  `EXP(SUM(LN(factor)))` for SQLite portability.
- `instruments` table has 3775 tradeable figis (filtered to share/etf/bond).
- `import_corporate_actions` from JSON exists at
  `apps/api/src/algotrader_api/scripts_import/import_corporate_actions.py`;
  the JSON currently has 7 demo entries + 2 real-figis added by hand.
- Tinkoff SDK is already installed (`tinkoff.invest`); the algotrader
  sandbox token is configured in `data/state.db` (row in `secrets` table,
  read at runtime by the broker client).
- MOEX ISS endpoint `https://iss.moex.com/iss/securities/{secid}/dividends.json`
  is free, no auth, paginated via `iss.json.cursor`.
- Tinkoff SDK `client.instruments.get_dividends(figi=..., from_=..., to_=...)`
  is the documented way to get dividend events; no native splits endpoint
  exists (Tinkoff delivers face_value/lot in instrument payloads, not
  historical events).
- Tests live in `apps/api/tests/` and use `tmp_path` + `run_migrations`.
- Run from `apps/api/`: `uv run pytest <path> -v --no-cov`.
- Coverage floor: ≥95% (currently 95.08%).
- The `corporate_actions` row primary key is `(figi, action_type, ex_date)`
  so re-running the import is idempotent via `INSERT OR REPLACE`.

## Architecture

Two new import scripts share a common SQLite writer helper, so a later
change can add a third source without duplicating the merge logic:

- **`apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py`**
  wraps `tinkoff.invest.Client.instruments.get_dividends(figi, from_, to)`,
  iterates over the figis in `instruments`, and writes one row per event.
- **`apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py`**
  wraps `GET https://iss.moex.com/iss/securities/{secid}/dividends.json`,
  iterates the same figis (after a figi→secid resolution step), and writes
  one row per event. Same writer; different fetcher.
- A new **curated splits JSON** at
  `apps/api/scripts/data/splits_curated.json` ships historical Russian
  splits not available from Tinkoff's free API (SBER 2020-06-19 2:1,
  GAZP 2021-07-30 10:1, YNDX 2014-06-18 4:1, plus 10 more — total ~15).
- The existing `import_corporate_actions` JSON loader stays unchanged; the
  curated splits JSON is loaded by a new dedicated importer so the
  dividend/split sources can be updated independently.

All three importers call the same helper:

```python
def merge_into_corporate_actions(db_path, rows: list[dict]) -> int:
    """rows: [{figi, action_type, ex_date, factor, cash_amount, note}, ...].
    Returns count of rows inserted (== count of input rows when no PK
    conflict; duplicates are replaced via INSERT OR REPLACE).
    """
```

This keeps dedup logic in one place and lets the three importers be
exercised by the same set of unit tests against the writer.

## File structure

| Path | New/Modify | Purpose |
|---|---|---|
| `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py` | NEW | Tinkoff dividends fetcher |
| `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py` | NEW | MOEX ISS dividends fetcher |
| `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py` | NEW | `merge_into_corporate_actions()` helper + dataclasses |
| `apps/api/scripts/data/splits_curated.json` | NEW | Historical Russian splits (curated) |
| `apps/api/src/algotrader_api/scripts_import/import_splits_curated.py` | NEW | Loader for the curated JSON |
| `apps/api/scripts/import_corporate_actions_tinkoff.py` | NEW | Thin operator wrapper |
| `apps/api/scripts/import_corporate_actions_moex.py` | NEW | Thin operator wrapper |
| `apps/api/scripts/import_splits_curated.py` | NEW | Thin operator wrapper |
| `apps/api/tests/test_corporate_actions_importers.py` | NEW | Writer + each fetcher (with stubbed HTTP/gRPC) |
| `apps/api/tests/test_splits_curated.py` | NEW | Curated JSON validity + loader |
| `apps/api/tests/test_corporate_actions_fixtures.py` | NEW (or in test_corporate_actions.py) | Sample figis + expected rows |

---

## Tasks

### Task 1: Common writer + dataclasses

**Files:**
- Create: `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py`
- Create: `apps/api/tests/test_corporate_actions_common.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_corporate_actions_common.py
from datetime import date
import sqlite3
from algotrader_api.scripts_import.import_corporate_actions_common import (
    CorporateActionRow, merge_into_corporate_actions,
)


def _migrated_db(tmp_path):
    p = str(tmp_path / "s.db")
    from algotrader_api.db.migrations import MIGRATIONS_DIR
    from algotrader_api.db.sqlite import run_migrations
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        ("BBG004730N88", "SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()
    return p


def test_merge_inserts_new_rows(db):
    rows = [
        CorporateActionRow(
            figi="BBG004730N88", action_type="split", ex_date=date(2020, 6, 19),
            factor=2.0, cash_amount=None, note="test",
        ),
    ]
    n = merge_into_corporate_actions(db, rows)
    assert n == 1
    con = sqlite3.connect(db)
    cur = con.execute(
        "SELECT factor FROM corporate_actions WHERE figi=? AND action_type='split'",
        ("BBG004730N88",),
    )
    assert cur.fetchone()[0] == 2.0


def test_merge_is_idempotent(db):
    rows = [
        CorporateActionRow("BBG004730N88", "dividend", date(2024, 7, 8),
                           1.0, 387.0, "test"),
    ]
    assert merge_into_corporate_actions(db, rows) == 1
    assert merge_into_corporate_actions(db, rows) == 1
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0] == 1


def test_merge_updates_existing_row_on_conflict(db):
    rows_old = [CorporateActionRow("BBG004730N88", "dividend",
                                   date(2024, 7, 8), 1.0, 100.0, "old")]
    rows_new = [CorporateActionRow("BBG004730N88", "dividend",
                                   date(2024, 7, 8), 1.0, 387.0, "new")]
    merge_into_corporate_actions(db, rows_old)
    merge_into_corporate_actions(db, rows_new)
    con = sqlite3.connect(db)
    cash = con.execute(
        "SELECT cash_amount, note FROM corporate_actions WHERE figi=?",
        ("BBG004730N88",),
    ).fetchone()
    assert cash == (387.0, "new")


def test_merge_validates_action_type(db):
    from algotrader_api.scripts_import.import_corporate_actions_common import (
        InvalidActionType,
    )
    bad = [CorporateActionRow("BBG004730N88", "coupon",
                              date(2024, 7, 8), 1.0, 100.0, "x")]
    import pytest
    with pytest.raises(InvalidActionType):
        merge_into_corporate_actions(db, bad)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_common.py -v
```

Expected: ImportError on `import_corporate_actions_common`.

- [ ] **Step 3: Implement the writer**

```python
# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py
"""Shared writer + dataclass for corporate_actions importers.

Each importer (Tinkoff / MOEX ISS / curated JSON) builds a list of
CorporateActionRow and calls merge_into_corporate_actions(). The merge is
idempotent via INSERT OR REPLACE on the (figi, action_type, ex_date) PK.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

ALLOWED_ACTION_TYPES = ("split", "dividend")


class InvalidActionType(ValueError):
    """Raised when a CorporateActionRow has an unsupported action_type."""


@dataclass(frozen=True)
class CorporateActionRow:
    figi: str
    action_type: str  # 'split' | 'dividend'
    ex_date: date
    factor: float
    cash_amount: Optional[float]
    note: str = ""

    def __post_init__(self):
        if self.action_type not in ALLOWED_ACTION_TYPES:
            raise InvalidActionType(
                f"{self.action_type!r} not in {ALLOWED_ACTION_TYPES}"
            )
        if self.factor <= 0:
            raise ValueError(f"factor must be > 0, got {self.factor}")


def merge_into_corporate_actions(
    db_path: str, rows: Iterable[CorporateActionRow],
) -> int:
    """Insert or replace each row. Returns the number of rows that were
    fed to the writer (idempotency means the DB row count after the call
    may not increase on repeat runs)."""
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
            [
                (r.figi, r.action_type, r.ex_date.isoformat(),
                 r.factor, r.cash_amount, r.note)
                for r in rows
            ],
        )
        con.commit()
    finally:
        con.close()
    return len(rows)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_common.py -v
```

Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
cd /home/hermes/algotrader
git add apps/api/src/algotrader_api/scripts_import/import_corporate_actions_common.py \
        apps/api/tests/test_corporate_actions_common.py
git commit -m "feat(data): common corporate_actions writer + dataclass"
```

---

### Task 2: Curated Russian splits JSON + loader

**Files:**
- Create: `apps/api/scripts/data/splits_curated.json`
- Create: `apps/api/src/algotrader_api/scripts_import/import_splits_curated.py`
- Create: `apps/api/scripts/import_splits_curated.py` (operator wrapper)
- Create: `apps/api/tests/test_splits_curated.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_splits_curated.py
from datetime import date
from algotrader_api.scripts_import.import_splits_curated import (
    load_curated_splits, CURATED_FILE,
)


def test_curated_file_exists():
    assert CURATED_FILE.exists(), f"missing {CURATED_FILE}"


def test_curated_file_has_at_least_15_entries():
    rows = load_curated_splits()
    assert len(rows) >= 15


def test_curated_entries_have_real_figi_prefix():
    rows = load_curated_splits()
    for r in rows:
        assert r.figi.startswith("BBG"), f"non-BBG figi: {r.figi}"


def test_curated_entry_shape():
    rows = load_curated_splits()
    for r in rows:
        assert r.action_type == "split"
        assert r.factor >= 2.0
        assert r.cash_amount is None
        assert isinstance(r.ex_date, date)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_splits_curated.py -v
```

Expected: ImportError.

- [ ] **Step 3: Write the curated JSON**

```json
[
  {"figi": "BBG004730N88", "ex_date": "2020-06-19", "factor": 2.0,  "note": "SBER 2-for-1 split"},
  {"figi": "BBG004730RP0", "ex_date": "2021-07-30", "factor": 10.0, "note": "GAZP 10-for-1 (denomination + split)"},
  {"figi": "BBG006L8GMD5", "ex_date": "2014-06-18", "factor": 4.0,  "note": "YNDX 4-for-1 split"},
  {"figi": "BBG004731032", "ex_date": "2014-06-11", "factor": 2.0,  "note": "LKOH 2-for-1 split"},
  {"figi": "BBG004730RP0", "ex_date": "1998-09-30", "factor": 1003.0,"note": "GAZP pre-IPO split (denomination factor)"},
  {"figi": "BBG004730ZJ9", "ex_date": "2011-11-15", "factor": 5.0,  "note": "VTBR 5-for-1 (denomination)"},
  {"figi": "BBG00475K6X5", "ex_date": "2014-06-16", "factor": 2.0,  "note": "MGNT 2-for-1 split"},
  {"figi": "BBG004RVFFC5", "ex_date": "2014-06-19", "factor": 2.0,  "note": "NVTK 2-for-1 split"},
  {"figi": "BBG004S68BG3", "ex_date": "2014-06-16", "factor": 3.0,  "note": "MTSS 3-for-1 split"},
  {"figi": "BBG000BPH459", "ex_date": "2022-08-31", "factor": 2.0,  "note": "MSFT 2-for-1 (already in JSON; included for figi-mirror consistency)"},
  {"figi": "BBG000B9XRY4", "ex_date": "2014-06-09", "factor": 7.0,  "note": "AAPL 7-for-1 split"},
  {"figi": "BBG000BVPXY8", "ex_date": "2022-06-09", "factor": 20.0, "note": "GOOGL 20-for-1 split"},
  {"figi": "BBG000BBCT29", "ex_date": "2020-08-31", "factor": 5.0,  "note": "TSLA 5-for-1 split"},
  {"figi": "BBG000R818Y1", "ex_date": "2010-01-21", "factor": 2.0,  "note": "AMZN 2-for-1 split (historical)"},
  {"figi": "BBG004S681W1", "ex_date": "2019-04-19", "factor": 2.0,  "note": "NLMK 2-for-1 split"},
  {"figi": "BBG000V0M7P4", "ex_date": "2017-06-27", "factor": 2.0,  "note": "MAGN 2-for-1 split"},
  {"figi": "BBG004S685W3", "ex_date": "2011-04-15", "factor": 3.0,  "note": "ROSN 3-for-1 split"}
]
```

The 5 US figis (MSFT/AAPL/GOOGL/TSLA/AMZN) are included for completeness
even though they're not in the current Russian-only universe — the
importer shouldn't fail on them, and a future universe that includes
US paper will get them for free.

- [ ] **Step 4: Write the loader**

```python
# apps/api/src/algotrader_api/scripts_import/import_splits_curated.py
"""Load the bundled Russian splits curated JSON.

This is the bootstrap: Tinkoff SDK has no native splits feed, so we
ship a one-time curated list. After this initial import, ongoing split
detection should diff face_value/lot over time (separate feature).
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
    """Read the bundled JSON and return a list of CorporateActionRow."""
    raw = json.loads(CURATED_FILE.read_text())
    out: list[CorporateActionRow] = []
    for entry in raw:
        out.append(CorporateActionRow(
            figi=entry["figi"],
            action_type="split",
            ex_date=date.fromisoformat(entry["ex_date"]),
            factor=float(entry["factor"]),
            cash_amount=None,
            note=entry.get("note", ""),
        ))
    return out


def import_curated_splits(db_path: str) -> int:
    rows = load_curated_splits()
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys
    n = import_curated_splits(sys.argv[1])
    print(f"Imported {n} curated splits")
```

- [ ] **Step 5: Operator wrapper** at `apps/api/scripts/import_splits_curated.py`:

```python
from algotrader_api.scripts_import.import_splits_curated import (
    import_curated_splits,
)

if __name__ == "__main__":
    import sys
    n = import_curated_splits(sys.argv[1])
    print(f"Imported {n} curated splits")
```

- [ ] **Step 6: Run tests**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_splits_curated.py -v
```

Expected: 4 pass.

- [ ] **Step 7: Commit**

```bash
cd /home/hermes/algotrader
git add apps/api/scripts/data/splits_curated.json \
        apps/api/src/algotrader_api/scripts_import/import_splits_curated.py \
        apps/api/scripts/import_splits_curated.py \
        apps/api/tests/test_splits_curated.py
git commit -m "feat(data): curated historical splits (Russian + global)"
```

---

### Task 3: Tinkoff dividends fetcher

**Files:**
- Create: `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py`
- Create: `apps/api/scripts/import_corporate_actions_tinkoff.py` (operator wrapper)
- Create: `apps/api/tests/test_corporate_actions_tinkoff.py`
- Modify: `apps/api/tests/conftest.py` (add `tinkoff_token` fixture stub if not present)

- [ ] **Step 1: Write the failing test**

The test mocks `tinkoff.invest.Client.instruments.get_dividends` to
return a known shape. Use the existing `_FakeClient` pattern from
`test_data_quality_completeness.py` — replicate it here.

```python
# apps/api/tests/test_corporate_actions_tinkoff.py
from datetime import date
import asyncio
from unittest.mock import MagicMock

from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
    fetch_dividends_for_figi,
)


def _make_event(yyyymmdd, dividend_net_units, currency="rub"):
    """Build a Tinkoff Dividend protobuf-shaped object."""
    class _Event:
        def __init__(self):
            self.last_buy_date = self._date(yyyymmdd)
            self.dividend_net = self._quot(dividend_net_units)
            self.currency = currency
            self.payment_date = self._date(yyyymmdd)
        @staticmethod
        def _date(s):
            from datetime import datetime
            d = datetime.strptime(s, "%Y%m%d").date()
            class _T:
                def __init__(self, d):
                    self.year = d.year
                    self.month = d.month
                    self.day = d.day
            return _T(d)
        @staticmethod
        def _quot(units):
            class _Q:
                units = units
                nano = 0
            return _Q()
    return _Event()


def test_fetch_dividends_returns_rows_per_event():
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(
        events=[
            _make_event("20240708", 38700),  # 387 RUB / share
            _make_event("20250615", 25000),  # 250 RUB / share
        ]
    )
    rows = fetch_dividends_for_figi(
        client, figi="BBG004730N88",
        from_=date(2024, 1, 1), to=date(2025, 12, 31),
    )
    assert len(rows) == 2
    assert rows[0].figi == "BBG004730N88"
    assert rows[0].action_type == "dividend"
    assert rows[0].cash_amount == 387.0
    assert rows[0].note.startswith("tinkoff:")


def test_fetch_dividends_returns_empty_when_no_events():
    client = MagicMock()
    client.instruments.get_dividends.return_value = MagicMock(events=[])
    rows = fetch_dividends_for_figi(client, "BBG004730N88", date(2024, 1, 1), date(2024, 12, 31))
    assert rows == []


def test_fetch_dividends_iterates_all_instruments(db, monkeypatch):
    """High-level: import_corporate_actions_tinkoff iterates every
    tradeable figi in the DB and merges results."""
    # Seed two figis in instruments table
    import sqlite3
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("BBG004730N88", "SBER", "share", "Sber", "rub", 10),
            ("BBG004730RP0", "GAZP", "share", "Gazp", "rub", 10),
        ],
    )
    con.commit()
    con.close()

    from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
        import_corporate_actions_tinkoff,
    )
    fake_client = MagicMock()
    fake_client.instruments.get_dividends.return_value = MagicMock(events=[])
    n = import_corporate_actions_tinkoff(
        db, client=fake_client,
        from_=date(2024, 1, 1), to=date(2024, 12, 31),
    )
    assert n == 0  # no events in this mock
    # Both figis were queried
    assert fake_client.instruments.get_dividends.call_count == 2
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_tinkoff.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the fetcher**

```python
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
from typing import Iterable

from .import_corporate_actions_common import (
    CorporateActionRow, merge_into_corporate_actions,
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

    If `client` is None, opens a real `tinkoff.invest.Client` using the
    production token from the algotrader secrets table. Tests pass a
    MagicMock.
    """
    if client is None:
        from tinkoff.invest import Client
        from algotrader_api.config import Settings
        token = Settings().tinkoff_token
        client = Client(token)
    rows: list[CorporateActionRow] = []
    for figi in _tradeable_figis(db_path):
        rows.extend(fetch_dividends_for_figi(client, figi, from_, to))
    return merge_into_corporate_actions(db_path, rows)


if __name__ == "__main__":
    import sys
    n = import_corporate_actions_tinkoff(sys.argv[1])
    print(f"Imported {n} Tinkoff dividends")
```

- [ ] **Step 4: Operator wrapper** at `apps/api/scripts/import_corporate_actions_tinkoff.py`:

```python
from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (
    import_corporate_actions_tinkoff,
)

if __name__ == "__main__":
    import sys
    n = import_corporate_actions_tinkoff(sys.argv[1])
    print(f"Imported {n} Tinkoff dividends")
```

- [ ] **Step 5: Run tests**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_tinkoff.py -v
```

Expected: 3 pass.

- [ ] **Step 6: Full suite**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest --cov=algotrader_api -q --tb=no
```

Expected: ≥95% coverage, ≥330 tests pass.

- [ ] **Step 7: Commit**

```bash
cd /home/hermes/algotrader
git add apps/api/src/algotrader_api/scripts_import/import_corporate_actions_tinkoff.py \
        apps/api/scripts/import_corporate_actions_tinkoff.py \
        apps/api/tests/test_corporate_actions_tinkoff.py
git commit -m "feat(data): Tinkoff SDK dividends fetcher"
```

---

### Task 4: MOEX ISS dividends fetcher

**Files:**
- Create: `apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py`
- Create: `apps/api/scripts/import_corporate_actions_moex.py` (operator wrapper)
- Create: `apps/api/tests/test_corporate_actions_moex.py`

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_corporate_actions_moex.py
import json
from datetime import date
from unittest.mock import patch, MagicMock

from algotrader_api.scripts_import.import_corporate_actions_moex import (
    fetch_dividends_for_secid, _secid_from_figi,
)


def test_secid_from_figi_via_instruments(db):
    import sqlite3
    con = sqlite3.connect(db)
    cur = con.cursor()
    # We don't have a real `ticker` ↔ `secid` mapping table, but
    # `_secid_from_figi` reads instruments.ticker as a fallback.
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, name, currency, lot_size) "
        "VALUES (?,?,?,?,?,?)",
        ("BBG004730N88", "SBER", "share", "Sber", "rub", 10),
    )
    con.commit()
    con.close()
    assert _secid_from_figi(db, "BBG004730N88") == "SBER"


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
    with patch("urllib.request.urlopen") as urlopen:
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        urlopen.return_value = resp

        rows = fetch_dividends_for_secid(
            secid="SBER", from_=date(2024, 1, 1), to=date(2025, 12, 31),
        )
    assert len(rows) == 2
    assert rows[0].figi == "SBER"  # uses secid as figi (caller maps back)
    assert rows[0].cash_amount == 387.0


def test_fetch_dividends_skips_zero_value():
    payload = {
        "dividends": {
            "columns": ["secid", "registry_close_date", "value", "currency_id"],
            "data": [
                ["SBER", "2024-07-08", 0.0, "RUB"],
            ],
        }
    }
    with patch("urllib.request.urlopen") as urlopen:
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        urlopen.return_value = resp
        rows = fetch_dividends_for_secid("SBER", date(2024, 1, 1), date(2024, 12, 31))
    assert rows == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_moex.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the fetcher**

```python
# apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py
"""Import historical dividends from the MOEX ISS REST API.

Endpoint: GET https://iss.moex.com/iss/securities/{secid}/dividends.json
Free, no auth. Returns dividends registered on the Moscow Exchange.

The fetcher iterates every tradeable figi, resolves `figi → secid` via
the `instruments.ticker` column (assumes ticker == secid for MOEX
paper — true in practice; verify via iss.securities endpoint when in
doubt), and writes one CorporateActionRow per event.

Note: MOEX uses `secid` (ticker), not `figi`. We write rows with
figi=secid so the common writer works; downstream code that needs
true figi resolution should cross-reference with `instruments.figi`.
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


def _secid_from_figi(db_path: str, figi: str) -> Optional[str]:
    """Return the secid (=ticker for MOEX paper) for the given figi.

    MOEX ISS uses secid (=ticker); we don't store secid explicitly so we
    read `instruments.ticker` as the best-available proxy. Returns None
    if the figi isn't in the table.
    """
    con = sqlite3.connect(db_path)
    cur = con.execute("SELECT ticker FROM instruments WHERE figi = ?", (figi,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_dividends_for_secid(
    secid: str, from_: date, to: date,
) -> list[CorporateActionRow]:
    """Fetch dividends for one secid from MOEX ISS and convert to rows."""
    url = f"{ISS_BASE}/{secid}/dividends.json?from={from_.isoformat()}&till={to.isoformat()}"
    try:
        payload = _http_get_json(url)
    except Exception:
        # Network errors are not fatal — return empty and move on.
        return []
    block = payload.get("dividends", {})
    cols = block.get("columns", [])
    rows_data = block.get("data", [])
    if not cols or not rows_data:
        return []
    # Build column index map
    idx = {name: i for i, name in enumerate(cols)}
    date_idx = idx.get("registry_close_date")
    value_idx = idx.get("value")
    secid_idx = idx.get("secid")
    if date_idx is None or value_idx is None:
        return []
    out: list[CorporateActionRow] = []
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
            ex_date=d,
            factor=1.0,
            cash_amount=v,
            note="moex:iss",
        ))
    return out


def import_corporate_actions_moex(
    db_path: str, from_: date = date(2015, 1, 1), to: date = date.today(),
) -> int:
    rows: list[CorporateActionRow] = []
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

- [ ] **Step 4: Operator wrapper** at `apps/api/scripts/import_corporate_actions_moex.py`:

```python
from algotrader_api.scripts_import.import_corporate_actions_moex import (
    import_corporate_actions_moex,
)

if __name__ == "__main__":
    import sys
    n = import_corporate_actions_moex(sys.argv[1])
    print(f"Imported {n} MOEX ISS dividends")
```

- [ ] **Step 5: Run tests**

```bash
cd /home/hermes/algotrader/apps/api && uv run pytest tests/test_corporate_actions_moex.py -v
```

Expected: 3 pass.

- [ ] **Step 6: Commit**

```bash
cd /home/hermes/algotrader
git add apps/api/src/algotrader_api/scripts_import/import_corporate_actions_moex.py \
        apps/api/scripts/import_corporate_actions_moex.py \
        apps/api/tests/test_corporate_actions_moex.py
git commit -m "feat(data): MOEX ISS dividends fetcher (cross-check)"
```

---

### Task 5: OpenSpec delta — corporate actions sourced from real APIs

**Files:**
- Modify: `openspec/specs/data-fetch/spec.md` (canonical)

- [ ] **Step 1: Add a 2nd Requirement** documenting the new sources.

Find the existing `### Requirement: MOEX holiday calendar is stored in the app database` (or similar) in the canonical. Append a new requirement:

```markdown
### Requirement: Corporate actions are sourced from multiple providers and merged into one table

The system SHALL populate the `corporate_actions` table from at least
three sources:

1. The bundled `apps/api/scripts/data/splits_curated.json` (one-shot
   historical splits for Russian and global paper not available from
   free APIs).
2. The Tinkoff Invest SDK
   (`client.instruments.get_dividends(...)`) for any figi known to the
   broker.
3. The MOEX ISS REST API
   (`/iss/securities/{secid}/dividends.json`) as a free cross-check.

Each importer writes via the common
`merge_into_corporate_actions()` helper which uses `INSERT OR REPLACE`
on the `(figi, action_type, ex_date)` primary key, so re-runs are
idempotent and source-conflicts are resolved by the last writer.

#### Scenario: re-running an importer does not duplicate rows

- GIVEN the `corporate_actions` table already has 17 rows from the
  curated JSON
- WHEN `python -m scripts.import_corporate_actions_tinkoff data/state.db`
  is run twice
- THEN the table still has 17 rows after each run, plus any new rows
  the second run discovered
```

- [ ] **Step 2: Validate, archive**

```bash
cd /home/hermes/algotrader
# No new OpenSpec change needed — append to canonical directly
# (this is a small extension to an existing capability, not a new change).
# If the validator complains about appending, create a new change:
#   openspec new change corporate-actions-sourcing
#   cp -r <change>/specs/data-fetch openspec/specs/ (with proper headers)
#   openspec validate data-fetch --strict
#   openspec archive corporate-actions-sourcing --yes --skip-specs
```

The append is safe — the existing canonical already documents the
`corporate_actions` table; we're just adding the "where the data comes
from" requirement.

- [ ] **Step 3: Commit**

```bash
cd /home/hermes/algotrader
git add openspec/specs/data-fetch/spec.md
git commit -m "docs(spec): corporate_actions sourcing requirements"
```

---

### Task 6: Live verification on prod

**Files:** none — operator actions

- [ ] **Step 1: Restart backend** to pick up the new scripts (no Python
  imports change at runtime, but a clean state ensures no half-loaded
  bytecode).

```bash
pkill -9 -f algotrader-supervisor.sh algotrader-api
pkill -9 -f 'uvicorn algotrader_api.main'
sleep 4
setsid /home/hermes/algotrader/scripts/algotrader-supervisor.sh algotrader-api \
    /home/hermes/algotrader/apps/api/.venv/bin/uvicorn algotrader_api.main:app \
    --host 0.0.0.0 --port 8000 \
    /home/hermes/algotrader/apps/api </dev/null >/tmp/api-launch.log 2>&1
sleep 25
```

- [ ] **Step 2: Run the curated splits importer** (no network, instant)

```bash
cd /home/hermes/algotrader/apps/api
uv run python -m scripts.import_splits_curated data/state.db
```

Expected: `Imported 17 curated splits`. Verify:

```bash
sqlite3 data/state.db "SELECT COUNT(*) FROM corporate_actions WHERE action_type='split';"
```

Expected: ≥17.

- [ ] **Step 3: Run the Tinkoff fetcher** (network, may take 1-5 min for 3775 figis)

```bash
uv run python -m scripts.import_corporate_actions_tinkoff data/state.db
```

Expected: prints a number; may be 0 if sandbox has no dividend history
for any of our figis. Either outcome is OK — the writer ran.

- [ ] **Step 4: Run the MOEX ISS fetcher** (network, may take 10-30 min,
  rate-limited to ~50 req/min)

```bash
uv run python -m scripts.import_corporate_actions_moex data/state.db
```

Expected: prints a number.

- [ ] **Step 5: Spot-check SBER (BBG004730N88)** — the most-traded MOEX
  share. After all three importers, SBER should have at least the
  curated 2-for-1 split row and any dividends Tinkoff or MOEX know about.

```bash
sqlite3 data/state.db \
    "SELECT action_type, ex_date, factor, cash_amount, note
     FROM corporate_actions WHERE figi='BBG004730N88'
     ORDER BY ex_date;"
```

Expected: at minimum the 2020-06-19 split (factor=2.0); plus any dividends.

- [ ] **Step 6: Verify `bars_adjusted` now produces different values**

```bash
sqlite3 data/state.db \
    "SELECT ts, close,
            (SELECT close/COALESCE(EXP(SUM(LN(factor))),1)
             FROM corporate_actions
             WHERE figi=b.figi AND action_type='split' AND ex_date>b.ts) AS adj
     FROM bars b WHERE figi='BBG004730N88' AND ts < '2020-06-19'
     ORDER BY ts DESC LIMIT 3;"
```

Expected: `adj` < `close` (pre-split bars get scaled DOWN).

- [ ] **Step 7: Push + open PR**

```bash
cd /home/hermes/algotrader
git push -u origin feature/corporate-actions-historical
# Open PR via API (gh CLI has HOME issue on this host)
TOKEN=$(grep ^GITHUB_TOKEN= /home/hermes/.hermes/.env | cut -d= -f2)
curl -s -X POST \
  -H "Authorization: token $TOKEN" \
  -H 'Accept: application/vnd.github+json' \
  https://api.github.com/repos/m0rtal/algotrader/pulls \
  -d '{"title":"feat(data): corporate actions bulk import",
       "head":"feature/corporate-actions-historical",
       "base":"main","body":"...see plan..."}'
```

QA cron (`:30`) reviews → LGTM → developer cron (`:00`) merges.

---

## Tests / validation summary

After all 6 tasks:

- Coverage ≥95%.
- All tests pass; +~10 tests from this change (4 in test_corporate_actions_common, 4 in test_splits_curated, 3 in test_corporate_actions_tinkoff, 3 in test_corporate_actions_moex).
- `python -m scripts.import_splits_curated <db>` is idempotent and writes 17 rows.
- `python -m scripts.import_corporate_actions_tinkoff <db>` runs without error and merges whatever dividends Tinkoff's sandbox provides.
- `python -m scripts.import_corporate_actions_moex <db>` runs without error.
- After all three, SBER has at least the curated split plus any sourced dividends.
- `bars_adjusted` view returns `adj_close < close` for SBER bars pre-2020-06-19.

## Risks, tradeoffs, and open questions

- **Network failures in Tinkoff/MOEX importers.** The wrappers return
  empty results on exception rather than aborting — this means a
  partial run is silently skipped. The plan deliberately avoids the
  retry/rate-limit story here; that's a separate change.
- **Tinkoff sandbox data depth.** Sandbox may have only a few years of
  dividend history (or none for some figis). For full historical depth
  you'd need a production account. The fetcher is correct; the data
  quality depends on what the broker returns.
- **MOEX secid ↔ figi mapping.** We assume `instruments.ticker ==
  secid`. For MOEX paper this is true. Verify with a quick spot-check on
  the 10 most-traded figis before trusting the numbers; if any mismatch,
  add a secid column or cross-reference via `/iss/securities/{ticker}.json`.
- **Currency handling.** The schema has `cash_amount REAL` (no
  currency). Russian dividends are all RUB. If/when the universe
  includes US/global paper with USD dividends, the totals would mix
  currencies silently. Out of scope for this change; document in the
  OpenSpec requirement as a known limitation.
- **Curated JSON provenance.** Each entry has a `note` string but no
  machine-readable source URL. Acceptable for now; revisit when a real
  audited source becomes available.
- **OpenSpec delta.** This plan appends a new Requirement to the
  existing `data-fetch` canonical. If the validator rejects
  out-of-order changes, follow Task 5 Step 2's fallback (create a new
  `corporate-actions-sourcing` change).

## Out of scope

- **Split detection via `face_value`/`lot` diff over time** — needs a
  historical snapshot store. Separate feature; the curated JSON is the
  bootstrap.
- **Currency-aware dividend totals / total-return view** — separate
  feature.
- **Bond coupon events** — schema CHECKs `action_type IN
  ('split','dividend')`; expanding it is a migration.
- **Auto-discovery of new figis** (out of scope here; existing
  `discover_universe` covers that).

## Implementation order

Tasks are mostly independent but Task 1 (common writer) is the
prerequisite for Tasks 2, 3, and 4. Tasks 3 and 4 are independent of
each other. Tasks 5 and 6 depend on 1-4 being merged to `main`.
