# Data Completeness Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect and backfill historical gaps in figi bars data so every tradeable figi has a contiguous daily series from its first bar to today.

**Architecture:** New `moex_holidays` table for trading-day calendar. New `data_quality.completeness` module: gap detection walks `bars` in order, finds intervals > min_gap_days (default 5), broker fetches each interval, atomic write via existing `replace_bars_for_figi`. New `HealthIssue.INCOMPLETE_HISTORY` sub-problem in health. Guardian `run_daily_guardian` calls `run_completeness_pass` after `recover_stale`.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest. Tinkoff Invest SDK via async wrapper. No new dependencies.

**Spec:** `openspec/changes/archive/2026-09-13-data-completeness-backfill/` (proposal + design + tasks)

## Global Constraints

- Coverage floor: ≥95% per `SPEC.md` ≥95% rule
- All new code TDD-first (test fails before impl passes)
- Backfill never overwrites bars outside the detected gap range
- Idempotent: running completeness pass twice produces no duplicate bars
- Sequential per-figi broker calls (no fan-out — rate-limit aware)
- Skip `completeness_exhausted` figis on subsequent runs
- Pre-commit hook runs prettier on staged .md files (already passing)
- Secrets: GITHUB_TOKEN in `/home/hermes/.hermes/.env` (never logged)

---

## File Structure

| File                                                              | Responsibility                                                                             |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql` | DDL: `moex_holidays(date, name)`                                                           |
| `apps/api/scripts/data/moex_holidays.json`                        | Static 2020-2027 calendar                                                                  |
| `apps/api/scripts/import_moex_holidays.py`                        | One-shot loader into SQLite                                                                |
| `apps/api/src/algotrader_api/data_quality/completeness.py`        | NEW: `find_gap_intervals`, `backfill_gaps`, `run_completeness_pass`, `CompletenessSummary` |
| `apps/api/src/algotrader_api/data_quality/health.py`              | UPDATE: `HealthIssue.INCOMPLETE_HISTORY` + holiday-aware expected count                    |
| `apps/api/src/algotrader_api/data_quality/service.py`             | UPDATE: chain completeness pass after `recover_stale`                                      |
| `apps/api/src/algotrader_api/data_quality/__init__.py`            | UPDATE: re-export new symbols                                                              |
| `apps/api/src/algotrader_api/routes/data_quality.py`              | UPDATE: serialize `INCOMPLETE_HISTORY` in drill-down response                              |
| `apps/api/tests/test_data_quality_completeness.py`                | NEW: 8 cases for completeness module                                                       |
| `apps/api/tests/test_data_quality_health.py`                      | UPDATE: 1 new case for INCOMPLETE_HISTORY                                                  |
| `apps/api/tests/test_data_quality_service.py`                     | UPDATE: 1 new case for chained pass                                                        |
| `apps/api/tests/test_moex_holidays.py`                            | NEW: import script tests                                                                   |

---

## Task 1: MOEX holidays table + import script

**Files:**

- Create: `apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql`
- Create: `apps/api/scripts/data/moex_holidays.json`
- Create: `apps/api/scripts/import_moex_holidays.py`
- Test: `apps/api/tests/test_moex_holidays.py`

**Interfaces:**

- Produces: `moex_holidays(date TEXT PRIMARY KEY, name TEXT NOT NULL)` table
- `import_moex_holidays(db_path: str) -> int` (returns rows written)

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_moex_holidays.py
from algotrader_api.scripts_import.import_moex_holidays import import_moex_holidays

def test_import_creates_holidays_table(tmp_path):
    db = tmp_path / "test.db"
    n = import_moex_holidays(str(db))
    assert n >= 70  # 2020..2027 inclusive
    import sqlite3
    con = sqlite3.connect(str(db))
    rows = con.execute("SELECT date, name FROM moex_holidays").fetchall()
    assert rows[0][0]  # first row has date
    assert rows[0][1]  # first row has name

def test_import_is_idempotent(tmp_path):
    db = tmp_path / "test.db"
    import_moex_holidays(str(db))
    first_count = _count(str(db))
    import_moex_holidays(str(db))
    second_count = _count(str(db))
    assert first_count == second_count

def _count(db_path):
    import sqlite3
    con = sqlite3.connect(db_path)
    return con.execute("SELECT COUNT(*) FROM moex_holidays").fetchone()[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && uv run pytest tests/test_moex_holidays.py -v
```

Expected: ModuleNotFoundError on `algotrader_api.scripts_import`.

- [ ] **Step 3: Create the migration file**

```sql
-- apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql
CREATE TABLE IF NOT EXISTS moex_holidays (
    date TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moex_holidays_date ON moex_holidays(date);
```

- [ ] **Step 4: Create the static JSON calendar**

```json
[
  {"date": "2020-01-01", "name": "New Year"},
  {"date": "2020-01-02", "name": "New Year holiday"},
  ...up to 2027-12-31 (see scripts/data/moex_holidays.json for full list, ~75 entries)
]
```

- [ ] **Step 5: Write the import script**

```python
# apps/api/scripts/import_moex_holidays.py
"""Load MOEX trading-day calendar into moex_holidays table.
Idempotent: safe to run multiple times.
"""
import json
import sqlite3
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "moex_holidays.json"

def import_moex_holidays(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS moex_holidays (
            date TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
    """)
    holidays = json.loads(DATA_FILE.read_text())
    cur.executemany(
        "INSERT OR REPLACE INTO moex_holidays(date, name) VALUES (?, ?)",
        [(h["date"], h["name"]) for h in holidays],
    )
    con.commit()
    con.close()
    return len(holidays)

if __name__ == "__main__":
    import sys
    n = import_moex_holidays(sys.argv[1])
    print(f"Imported {n} MOEX holidays")
```

- [ ] **Step 6: Run tests**

```bash
cd apps/api && uv run pytest tests/test_moex_holidays.py -v
```

Expected: 2 pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/algotrader_api/db/migrations/006_moex_holidays.sql \
        apps/api/scripts/data/moex_holidays.json \
        apps/api/scripts/import_moex_holidays.py \
        apps/api/tests/test_moex_holidays.py
git commit -m "feat(moex-holidays): import script + migration 006"
```

---

## Task 2: Health module — INCOMPLETE_HISTORY + holiday-aware count

**Files:**

- Modify: `apps/api/src/algotrader_api/data_quality/health.py`
- Modify: `apps/api/tests/test_data_quality_health.py`

**Interfaces:**

- Consumes: `moex_holidays` table (from Task 1)
- Produces: `HealthIssue.INCOMPLETE_HISTORY` enum value
- New helper: `_weekdays_excluding_holidays(start: date, end: date, con) -> int`
- `compute_health` reports include `INCOMPLETE_HISTORY` issue when `actual < expected * 0.95`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_quality_health.py`:

```python
def test_health_report_marks_incomplete_history_with_holidays(db, today):
    from algotrader_api.db.sqlite import get_connection
    from algotrader_api.data_quality.health import (
        HealthIssue, HealthReport, compute_health,
    )

    figi = "FIGI-SPARSE"
    cur = get_connection(db).cursor()
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?)",
        (figi, "SPRS", "share", "RUB", 1),
    )
    # Insert a sparse history: one bar at day 0, then a 14-day hole, then 5 more bars
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        [
            (figi, "2024-03-01", 100, 101, 99, 100, 1000),
            (figi, "2024-03-25", 110, 111, 109, 110, 2000),
            (figi, "2024-04-01", 112, 113, 111, 112, 3000),
            (figi, "2024-04-08", 113, 114, 112, 113, 4000),
            (figi, "2024-04-15", 114, 115, 113, 114, 5000),
            (figi, "2024-04-22", 115, 116, 114, 115, 6000),
        ],
    )
    cur.connection.commit()

    # Insert a few MOEX holidays inside the range to validate subtraction
    cur.executemany(
        "INSERT INTO moex_holidays(date, name) VALUES (?, ?)",
        [("2024-03-08", "test"), ("2024-05-09", "test")],
    )
    cur.connection.commit()

    report = compute_health(db, figi, today=today)
    assert HealthIssue.INCOMPLETE_HISTORY in report.issues
    assert report.health_score <= 75  # penalty -25
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && uv run pytest tests/test_data_quality_health.py::test_health_report_marks_incomplete_history_with_holidays -v
```

Expected: AttributeError on `HealthIssue.INCOMPLETE_HISTORY`.

- [ ] **Step 3: Add `INCOMPLETE_HISTORY` to the enum**

In `data_quality/health.py`, add to the existing `HealthIssue` enum:

```python
class HealthIssue(Enum):
    SPARSE_HISTORY = "sparse-history"
    HAS_GAPS = "has-gaps"
    MISSING_RECENT_DAYS = "missing-recent-days"
    ORPHAN_OK = "orphan-ok"
    INCOMPLETE_HISTORY = "incomplete-history"  # NEW
```

- [ ] **Step 4: Add the helper `_weekdays_excluding_holidays`**

```python
def _weekdays_excluding_holidays(start: date, end: date, con) -> int:
    """Count weekdays between start and end (inclusive), minus MOEX holidays in range."""
    if end < start:
        return 0
    weekdays = sum(1 for i in range((end - start).days + 1)
                   if (start + timedelta(days=i)).weekday() < 5)
    rows = con.execute(
        "SELECT date FROM moex_holidays WHERE date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return weekdays - len(rows)
```

- [ ] **Step 5: Wire it into `compute_health`**

In `compute_health`, replace the existing expected-count computation:

```python
# Find first_bar (already computed earlier in this function as `first`).
# After existing gap/missing-recent checks, add:
expected = _weekdays_excluding_holidays(first, today, con)
if expected > 0 and actual < expected * 0.95:
    issues.append(HealthIssue.INCOMPLETE_HISTORY)
    penalty += 25
```

The new logic must append `INCOMPLETE_HISTORY` before the final `health_score = max(0, 100 - penalty)` calculation.

- [ ] **Step 6: Run test to verify it passes**

```bash
cd apps/api && uv run pytest tests/test_data_quality_health.py -v
```

Expected: all pass including the new one.

- [ ] **Step 7: Run full test suite**

```bash
cd apps/api && uv run pytest -q --tb=no
```

Expected: 308+ pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/health.py \
        apps/api/tests/test_data_quality_health.py
git commit -m "feat(health): INCOMPLETE_HISTORY sub-problem with holiday-aware expected count"
```

---

## Task 3: completeness module — `find_gap_intervals`

**Files:**

- Create: `apps/api/src/algotrader_api/data_quality/completeness.py`
- Modify: `apps/api/src/algotrader_api/data_quality/__init__.py`
- Modify: `apps/api/tests/test_data_quality_completeness.py` (create)

**Interfaces:**

- `find_gap_intervals(db, figi, min_gap_days=5) -> list[tuple[date, date]]`
  Returns gaps as `(start, end)` where `start` is the date of the bar
  before the gap and `end` is the date of the bar after the gap.
- Uses `moex_holidays` for adjustment.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_data_quality_completeness.py
from datetime import date
from algotrader_api.data_quality.completeness import find_gap_intervals


def test_find_gap_intervals_returns_empty_for_complete_history(db):
    figi = "FIGI-FULL"
    _seed_instrument_and_bars(db, figi, [
        "2024-01-10", "2024-01-15", "2024-01-22", "2024-01-29",
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_returns_gap_for_14_day_hole(db):
    figi = "FIGI-GAP"
    _seed_instrument_and_bars(db, figi, [
        "2024-03-01",   # before the gap
        "2024-03-22",   # after the gap (21 calendar days = 14 trading days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert len(gaps) == 1
    start, end = gaps[0]
    assert start == date(2024, 3, 1)
    assert end == date(2024, 3, 22)


def test_find_gap_intervals_ignores_weekend_only_gap(db):
    figi = "FIGI-WEEKEND"
    # Friday → Monday: 3 calendar days, 1 trading day — below threshold
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-11",  # Monday
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []


def test_find_gap_intervals_handles_holiday_in_gap(db):
    figi = "FIGI-HOL"
    # 8 calendar days, 6 weekdays, but with a 3-day MOEX holiday inside: 3 trading days
    # — still below threshold.
    _seed_holidays(db, ["2024-03-12", "2024-03-13", "2024-03-14"])
    _seed_instrument_and_bars(db, figi, [
        "2024-03-08",  # Friday
        "2024-03-18",  # Monday (8 calendar days)
    ])
    gaps = find_gap_intervals(db, figi, min_gap_days=5)
    assert gaps == []  # holiday adjustment brought it below threshold
```

The `_seed_*` helpers are defined at the top of the test file:

```python
def _seed_instrument_and_bars(db, figi, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.execute(
        "INSERT INTO instruments(figi, ticker, class, currency, lot_size) "
        "VALUES (?, ?, ?, ?, ?)",
        (figi, "T" + figi[-3:], "share", "RUB", 1),
    )
    cur.executemany(
        "INSERT INTO bars(figi, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        [(figi, d, 100, 101, 99, 100, 1000) for d in dates],
    )
    cur.connection.commit()

def _seed_holidays(db, dates):
    from algotrader_api.db.sqlite import get_connection
    cur = get_connection(db).cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO moex_holidays(date, name) VALUES (?, ?)",
        [(d, "test") for d in dates],
    )
    cur.connection.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd apps/api && uv run pytest tests/test_data_quality_completeness.py -v
```

Expected: ImportError on `algotrader_api.data_quality.completeness`.

- [ ] **Step 3: Implement `find_gap_intervals`**

```python
# apps/api/src/algotrader_api/data_quality/completeness.py
"""Detect and backfill historical gaps in figi bars data."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

from ..db.sqlite import get_connection


def find_gap_intervals(
    db: str, figi: str, min_gap_days: int = 5,
) -> list[tuple[date, date]]:
    """Find intervals where bars are missing beyond `min_gap_days`
    trading days (excluding MOEX holidays in range).

    Returns a list of (start, end) tuples where:
    - `start` is the date of the last bar BEFORE the gap.
    - `end` is the date of the next bar AFTER the gap.

    The caller is responsible for fetching [start + 1, end] from the broker.
    """
    con = get_connection(db)
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
        (figi,),
    ).fetchall()
    if len(rows) < 2:
        return []
    bars = [date.fromisoformat(r[0]) for r in rows]
    gaps: list[tuple[date, date]] = []
    for prev, cur in zip(bars, bars[1:]):
        cal_days = (cur - prev).days
        # Subtract weekends and MOEX holidays from the calendar gap.
        weekdays = sum(
            1 for i in range(cal_days)
            if (prev + timedelta(days=i + 1)).weekday() < 5
        )
        holiday_rows = con.execute(
            "SELECT COUNT(*) FROM moex_holidays WHERE date > ? AND date < ?",
            (prev.isoformat(), cur.isoformat()),
        ).fetchone()
        trading_gap = weekdays - (holiday_rows[0] if holiday_rows else 0)
        if trading_gap >= min_gap_days:
            gaps.append((prev, cur))
    return gaps


# ... (continued in Task 4)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd apps/api && uv run pytest tests/test_data_quality_completeness.py::test_find_gap_intervals_returns_empty_for_complete_history \
                    tests/test_data_quality_completeness.py::test_find_gap_intervals_returns_gap_for_14_day_hole \
                    tests/test_data_quality_completeness.py::test_find_gap_intervals_ignores_weekend_only_gap \
                    tests/test_data_quality_completeness.py::test_find_gap_intervals_handles_holiday_in_gap -v
```

Expected: 4 pass.

- [ ] **Step 5: Re-export from `data_quality/__init__.py`**

```python
# apps/api/src/algotrader_api/data_quality/__init__.py
from .health import HealthIssue, HealthReport, compute_all, compute_health
from .completeness import (  # NEW
    CompletenessSummary, backfill_gaps, find_gap_intervals,
    run_completeness_pass,
)

__all__ = [
    "HealthIssue", "HealthReport", "compute_all", "compute_health",
    "CompletenessSummary", "backfill_gaps", "find_gap_intervals",
    "run_completeness_pass",
]
```

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/completeness.py \
        apps/api/src/algotrader_api/data_quality/__init__.py \
        apps/api/tests/test_data_quality_completeness.py
git commit -m "feat(completeness): find_gap_intervals with holiday-aware gap detection"
```

---

## Task 4: `backfill_gaps` + `run_completeness_pass`

**Files:**

- Modify: `apps/api/src/algotrader_api/data_quality/completeness.py`
- Modify: `apps/api/tests/test_data_quality_completeness.py`

**Interfaces:**

- `BrokerClient(Protocol)` — already exists in the project; needs
  `async get_candles(figi, date_from, date_to, interval) -> list[Candle]`.
- `backfill_gaps(client, figi, gaps, db) -> int` — sequential broker calls
  - atomic writes via `replace_bars_for_figi`.
- `run_completeness_pass(db, client, runner, reports) -> CompletenessSummary`.
- Exhaustion tracked via `pipeline` table with `status='completeness_exhausted'`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_quality_completeness.py`:

```python
import asyncio
from datetime import date
from unittest.mock import AsyncMock

from algotrader_api.data_quality.completeness import (
    backfill_gaps, run_completeness_pass, CompletenessSummary,
)
from algotrader_api.data_quality.health import HealthIssue, HealthReport


class _FakeClient:
    def __init__(self, candles_by_range):
        self.candles_by_range = candles_by_range
        self.calls = []

    async def get_candles(self, figi, date_from, date_to, interval):
        self.calls.append((figi, date_from, date_to))
        return self.candles_by_range.get((figi, date_from, date_to), [])


def _to_candle(date_str, close=100):
    """Build a Candle-like object that matches Tinkoff SDK shape."""
    class _C:
        time = type("T", (), {"year": int(date_str[:4]), "month": int(date_str[5:7]),
                              "day": int(date_str[8:10])})()
        open = 99
        high = 101
        low = 98
        close = close
        volume = 1000
    return _C()


def test_backfill_gaps_writes_only_in_gap_range(db):
    figi = "FIGI-BF"
    _seed_instrument_and_bars(db, figi, ["2024-03-01", "2024-03-22"])
    client = _FakeClient({(figi, "2024-03-02", "2024-03-22"):
                          [_to_candle(d) for d in [
                              "2024-03-04", "2024-03-05", "2024-03-06",
                              "2024-03-11", "2024-03-12", "2024-03-13",
                              "2024-03-14", "2024-03-18", "2024-03-19",
                              "2024-03-20", "2024-03-21",
                          ]]})
    added = asyncio.run(backfill_gaps(client, figi,
                                      [(date(2024, 3, 1), date(2024, 3, 22))],
                                      db))
    assert added == 11
    # Existing bars for 03-01 and 03-22 are not overwritten.
    from algotrader_api.db.sqlite import get_connection
    con = get_connection(db)
    rows = con.execute("SELECT ts FROM bars WHERE figi = ? ORDER BY ts",
                       (figi,)).fetchall()
    dates = [r[0] for r in rows]
    assert "2024-03-01" in dates
    assert "2024-03-22" in dates


def test_run_completeness_pass_marks_exhausted_when_no_bars_added(db):
    figi = "FIGI-EXH"
    _seed_instrument_and_bars(db, figi, ["2024-01-01", "2024-02-15"])
    # Seed an INCOMPLETE_HISTORY report
    report = HealthReport(
        figi=figi, ticker="EXH",
        health_score=70,
        issues={HealthIssue.INCOMPLETE_HISTORY, HealthIssue.HAS_GAPS},
        first_bar=date(2024, 1, 1),
        last_bar=date(2024, 2, 15),
    )
    # Broker returns nothing
    client = _FakeClient({})
    summary = asyncio.run(run_completeness_pass(
        db, client, runner=None, reports={figi: report},
    ))
    assert summary.exhausted == 1
    assert summary.gaps_found >= 1
    assert summary.bars_added == 0


def test_run_completeness_pass_skips_exhausted_figis(db):
    from algotrader_api.db.sqlite import get_connection
    figi = "FIGI-SKIP"
    _seed_instrument_and_bars(db, figi, ["2024-01-01", "2024-02-15"])
    # Mark it exhausted via the pipeline table (must exist in prod migration)
    con = get_connection(db)
    con.execute(
        "INSERT INTO pipeline(name, status, started_at) VALUES (?, ?, ?)",
        (f"completeness.{figi}", "completeness_exhausted", "2024-09-13"),
    )
    con.commit()
    report = HealthReport(
        figi=figi, ticker="SKP", health_score=70,
        issues={HealthIssue.INCOMPLETE_HISTORY},
        first_bar=date(2024, 1, 1), last_bar=date(2024, 2, 15),
    )
    client = _FakeClient({})
    summary = asyncio.run(run_completeness_pass(
        db, client, runner=None, reports={figi: report},
    ))
    # No broker calls — figi is skipped
    assert client.calls == []
    assert summary.figis_examined == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd apps/api && uv run pytest tests/test_data_quality_completeness.py -v
```

Expected: ImportError on `backfill_gaps` / `run_completeness_pass`.

- [ ] **Step 3: Implement `CompletenessSummary` + `backfill_gaps` + `run_completeness_pass`**

Append to `completeness.py`:

```python
from dataclasses import dataclass, field


@dataclass
class CompletenessSummary:
    figis_examined: int = 0
    gaps_found: int = 0
    bars_added: int = 0
    exhausted: int = 0


def _candle_date(c) -> date:
    return date(c.time.year, c.time.month, c.time.day)


async def backfill_gaps(
    client, figi: str, gaps: list[tuple[date, date]], db: str,
) -> int:
    """Fetch each gap interval from the broker and write bars.

    Returns the number of bars written across all gaps.
    Idempotent: re-running for the same gaps writes the same bars
    (UNIQUE(figi, ts) constraint rejects duplicates).
    """
    from ..db.bars_sqlite import replace_bars_for_figi

    today = date.today()
    total_added = 0
    for start, end in gaps:
        from_date = (start + timedelta(days=1)).isoformat()
        to_date = end.isoformat()
        candles = await client.get_candles(
            figi=figi, date_from=from_date, date_to=to_date,
            interval="CANDLE_INTERVAL_DAY",
        )
        # Only keep bars older than today (closed bars).
        closed = [c for c in candles if _candle_date(c) < today]
        bars_to_write = [
            {
                "ts": _candle_date(c).isoformat(),
                "open": float(c.open.units + c.open.nano / 1e9),
                "high": float(c.high.units + c.high.nano / 1e9),
                "low": float(c.low.units + c.low.nano / 1e9),
                "close": float(c.close.units + c.close.nano / 1e9),
                "volume": int(c.volume),
            }
            for c in closed
        ]
        if not bars_to_write:
            continue
        added = replace_bars_for_figi(db, figi, bars_to_write, replace=True)
        total_added += added
    return total_added


async def run_completeness_pass(
    db, client, runner, reports: dict[str, "HealthReport"],
) -> CompletenessSummary:
    """For each figi with INCOMPLETE_HISTORY, find gaps and backfill."""
    summary = CompletenessSummary()
    con = get_connection(db)
    for figi, report in reports.items():
        if HealthIssue.INCOMPLETE_HISTORY not in report.issues:
            continue
        # Skip exhausted
        row = con.execute(
            "SELECT 1 FROM pipeline WHERE name = ? AND status = 'completeness_exhausted' LIMIT 1",
            (f"completeness.{figi}",),
        ).fetchone()
        if row:
            continue
        gaps = find_gap_intervals(db, figi, min_gap_days=5)
        if not gaps:
            continue
        summary.figis_examined += 1
        summary.gaps_found += len(gaps)
        added = await backfill_gaps(client, figi, gaps, db)
        summary.bars_added += added
        if added == 0:
            # Broker has no data for the range — mark exhausted.
            con.execute(
                "INSERT OR REPLACE INTO pipeline(name, status, started_at) VALUES (?, ?, ?)",
                (f"completeness.{figi}", "completeness_exhausted",
                 date.today().isoformat()),
            )
            con.commit()
            summary.exhausted += 1
    return summary
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd apps/api && uv run pytest tests/test_data_quality_completeness.py -v
```

Expected: all 7 pass (4 from Task 3 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/completeness.py \
        apps/api/tests/test_data_quality_completeness.py
git commit -m "feat(completeness): backfill_gaps + run_completeness_pass with exhaustion"
```

---

## Task 5: Wire completeness pass into `run_daily_guardian`

**Files:**

- Modify: `apps/api/src/algotrader_api/data_quality/service.py`
- Modify: `apps/api/tests/test_data_quality_service.py`

**Interfaces:**

- Consumes: `run_completeness_pass(db, client, runner, reports)` from Task 4.
- Modifies `run_daily_guardian` to chain the pass after `recover_stale`.

- [ ] **Step 1: Read the existing `service.py`**

Read `apps/api/src/algotrader_api/data_quality/service.py`. Identify the
end of `run_daily_guardian` (just after `recover_stale` finishes and the
pipeline row is appended). That's where the completeness call goes.

- [ ] **Step 2: Write the failing test**

Add to `tests/test_data_quality_service.py`:

```python
def test_run_daily_guardian_chains_completeness_pass(db, today):
    """After recover_stale, the daily guardian runs run_completeness_pass."""
    from unittest.mock import AsyncMock, MagicMock
    from algotrader_api.data_quality.service import run_daily_guardian
    from algotrader_api.data_quality.completeness import CompletenessSummary
    from algotrader_api.data_quality.health import HealthIssue, HealthReport

    figi = "FIGI-CHAIN"
    # Seed an instrument with INCOMPLETE_HISTORY but no recent data missing.
    _seed_incomplete_history_figi(db, figi)
    report = HealthReport(
        figi=figi, ticker="CHN", health_score=70,
        issues={HealthIssue.INCOMPLETE_HISTORY},
        first_bar=date(2024, 1, 1), last_bar=today,
    )

    fake_client = MagicMock()
    fake_client.get_shares = AsyncMock(return_value=[])  # no new instruments
    fake_client.get_etfs = AsyncMock(return_value=[])
    fake_client.get_bonds = AsyncMock(return_value=[])
    fake_client.get_candles = AsyncMock(return_value=[])

    fake_runner = MagicMock()

    # Patch run_completeness_pass to a no-op recorder
    from algotrader_api.data_quality import service as svc_mod
    called_with = []
    async def _spy(db_, client_, runner_, reports_):
        called_with.append(reports_)
        return CompletenessSummary()
    svc_mod.run_completeness_pass = _spy

    asyncio.run(run_daily_guardian(db, fake_client, fake_runner))
    assert len(called_with) == 1
```

`_seed_incomplete_history_figi` inserts an instrument with sparse history. Add to `tests/test_data_quality_service.py`.

- [ ] **Step 3: Run test to verify it fails**

```bash
cd apps/api && uv run pytest tests/test_data_quality_service.py::test_run_daily_guardian_chains_completeness_pass -v
```

Expected: AssertionError on `len(called_with) == 1`.

- [ ] **Step 4: Wire completeness pass into `run_daily_guardian`**

In `service.py`, after the line that appends the `recover_stale` pipeline row, add:

```python
from .completeness import run_completeness_pass  # NEW import at top

# Inside run_daily_guardian, after recover_stale(...) finishes and its
# pipeline row is appended:
reports_for_completeness = {
    r.figi: r for r in reports  # reuse the existing list of HealthReports
}
completeness_summary = await run_completeness_pass(
    db_path, client, runner, reports_for_completeness,
)
_pipeline_log(db_path, "completeness_backfill",
              f"examined={completeness_summary.figis_examined} "
              f"gaps={completeness_summary.gaps_found} "
              f"bars_added={completeness_summary.bars_added} "
              f"exhausted={completeness_summary.exhausted}")
```

Use the existing `_pipeline_log` helper if present; otherwise write a small new helper at the bottom of `service.py` that does the same `INSERT INTO pipeline ...` as the rest of the file.

- [ ] **Step 5: Run test to verify it passes**

```bash
cd apps/api && uv run pytest tests/test_data_quality_service.py -v
```

Expected: all pass including the new one.

- [ ] **Step 6: Run full suite**

```bash
cd apps/api && uv run pytest --cov=algotrader_api -q --tb=no
```

Expected: ≥95% coverage, 311+ tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/service.py \
        apps/api/tests/test_data_quality_service.py
git commit -m "feat(guardian): chain completeness_backfill after recover_stale"
```

---

## Task 6: Drill-down endpoint serializes INCOMPLETE_HISTORY

**Files:**

- Modify: `apps/api/src/algotrader_api/routes/data_quality.py`
- Modify: `apps/api/tests/test_data_quality_route.py` (or wherever the route test lives)

- [ ] **Step 1: Read the existing route**

Find `_report_to_dict` or the serialization function. Confirm that
HealthIssue values are serialized as their `.value` (string). If they
are, no code change needed — INCOMPLETE_HISTORY surfaces automatically.

- [ ] **Step 2: Add a smoke test**

```python
def test_drill_down_includes_incomplete_history_issue(client, db):
    # Seed a figi with INCOMPLETE_HISTORY report
    ...
    r = client.get("/api/data-quality/FIGI-TEST")
    body = r.json()
    assert "incomplete-history" in body["issues"]
```

- [ ] **Step 3: Run test**

```bash
cd apps/api && uv run pytest tests/test_data_quality_route.py -v
```

Expected: pass.

- [ ] **Step 4: Commit (if any change)**

```bash
git add apps/api/src/algotrader_api/routes/data_quality.py \
        apps/api/tests/test_data_quality_route.py
git commit -m "test(drill-down): confirm INCOMPLETE_HISTORY serializes correctly"
```

---

## Task 7: Live verification on prod

**Files:** none (operator actions)

- [ ] **Step 1: Import holidays into prod DB**

```bash
cd /home/hermes/algotrader/apps/api && \
    python -m scripts.import_moex_holidays data/state.db
sqlite3 data/state.db "SELECT COUNT(*) FROM moex_holidays"
```

Expected: ~70-80 rows.

- [ ] **Step 2: Pick a known-gappy figi and inspect**

```bash
sqlite3 data/state.db \
    "SELECT figi, MIN(ts), MAX(ts), COUNT(*) FROM bars GROUP BY figi
     HAVING COUNT(*) < (julianday(MAX(ts)) - julianday(MIN(ts))) * 0.5
     LIMIT 5"
```

Pick one figi, note the first/last bar and gap positions.

- [ ] **Step 3: Run a one-off completeness pass (no guardian)**

In a small one-liner:

```bash
cd /home/hermes/algotrader/apps/api && \
uv run python -c "
import asyncio
from algotrader_api.config import Settings
from algotrader_api.brokers.tinkoff import TinkoffClient
from algotrader_api.data_quality.service import compute_health
from algotrader_api.data_quality.completeness import (
    find_gap_intervals, backfill_gaps,
)
db = 'data/state.db'
client = TinkoffClient(token=Settings().tinkoff_token)
figi = '<the figi>'
gaps = find_gap_intervals(db, figi)
print('gaps:', gaps)
added = asyncio.run(backfill_gaps(client, figi, gaps, db))
print('added:', added)
"
```

- [ ] **Step 4: Confirm bars grew**

```bash
sqlite3 data/state.db "SELECT COUNT(*) FROM bars WHERE figi = '<figi>'"
```

Expected: increased.

- [ ] **Step 5: Wait one full daily run (next 23:00 MSK) and tail log**

```bash
tail -30 /home/hermes/algotrader/logs/guardian.log | grep completeness
```

Expected: `completeness_backfill` row.

---

## Task 8: Final commit + push

- [ ] **Step 1: Run full coverage**

```bash
cd apps/api && uv run pytest --cov=algotrader_api -q --tb=no
```

Expected: ≥95%, 311+ tests.

- [ ] **Step 2: Commit any remaining uncommitted**

```bash
git status
# If anything left:
git add -A && git commit -m "feat(data-completeness): final wiring"
```

- [ ] **Step 3: Push**

```bash
cd /home/hermes/algotrader
export $(grep ^GITHUB_TOKEN= /home/hermes/.hermes/.env | xargs)
git push -f https://x-access-token:${GITHUB_TOKEN}@github.com/m0rtal/algotrader.git main:main
```

- [ ] **Step 4: Verify on remote**

Confirm last commit on GitHub matches local HEAD.
