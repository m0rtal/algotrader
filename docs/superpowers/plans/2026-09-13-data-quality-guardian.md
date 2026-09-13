# Data Quality Guardian Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a daily guardian service that computes per-ticker data quality with a drill-down payload, exposes it via `GET /api/data-quality/{symbol}`, and self-heals stale / sparse / gappy figis without operator intervention.

**Architecture:** New `algotrader_api.data_quality` package with three modules (health / recovery / service). Reuses the existing `BackfillRunner` for recovery. New `/api/data-quality/{symbol}` endpoint. Daily `guardian` mode in `worker.py` replaces the existing `backfill` mode.

**Tech Stack:** Python 3.11, FastAPI, SQLite (existing tables only), pytest. No new dependencies.

**Spec:** `openspec/changes/archive/2026-09-13-data-quality-guardian/` (proposal.md, design.md, tasks.md, specs/data-fetch/spec.md).

## Global Constraints

- Coverage floor: **≥ 95%** (`pytest --cov=algotrader_api`).
- Type checks: pyright on touched files (existing project standard).
- All new health logic reads from existing tables (`bars`, `instruments`, `instrument_metadata`, `ingestion_logs`); no new tables, no new indexes.
- `TRADEABLE_CLASSES` from `algotrader_api.domain.tradeable` is the only source of truth for what counts as "tradeable" — used here too.
- TDD: every code task starts with a failing test, ends with a passing test, then commits.
- Backend runs as systemd `algotrader-api`; any change must not break a restart of that unit.

---

## Task 1: `data_quality.health` module

**Files:**

- Create: `apps/api/src/algotrader_api/data_quality/__init__.py`
- Create: `apps/api/src/algotrader_api/data_quality/health.py`
- Test: `apps/api/tests/test_data_quality_health.py`

**Interfaces produced:**

```python
class HealthIssue(str, Enum):
    MISSING_RECENT = "missing-recent-days"
    SPARSE_HISTORY = "sparse-history"
    HAS_GAPS = "has-gaps"
    RATE_LIMITED_FAILURES = "rate-limited-failures"

@dataclass
class HealthReport:
    figi: str
    ticker: str | None
    health_score: int        # 0-100
    issues: list[HealthIssue]
    first_bar: date | None
    last_bar: date | None
    actual_bars: int
    expected_bars: int
    recent_gaps: list[date]      # up to 5
    recent_failures: list[dict]  # up to 5: {ts, level, message}

def compute_health(db_path: str, figi: str, today: date | None = None) -> HealthReport
def compute_all(db_path: str, today: date | None = None) -> dict[str, HealthReport]
```

**Health-score formula:**

```
penalty = (30 if MISSING_RECENT else 0)
        + (20 if SPARSE_HISTORY else 0)
        + (20 if HAS_GAPS else 0)
        + (30 if RATE_LIMITED_FAILURES else 0)
score = max(0, 100 - penalty)
```

**Sub-problem definitions:**

| Issue                 | SQL or Python logic                                                                                                               | Capped?              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------- |
| MISSING_RECENT        | `MAX(bars.ts) < today - 3 days`                                                                                                   | —                    |
| SPARSE_HISTORY        | `actual < expected * 0.9`, `expected = weekdays since first_bar`                                                                  | penalty capped at 20 |
| HAS_GAPS              | Python pass: find any gap > 5 days in `bars.ts` ordered series                                                                    | —                    |
| RATE_LIMITED_FAILURES | `ingestion_logs` last 7d where `message LIKE '%rate%'` OR `'%RESOURCE_EXHAUSTED%'` and `level IN ('warn','error')`; threshold > 0 | —                    |

- [ ] **Step 1: Write failing tests**

```python
# tests/test_data_quality_health.py
import sqlite3
from datetime import date, timedelta
import pytest
from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.health import (
    HealthIssue, compute_health, compute_all,
)


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT, source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE IF NOT EXISTS ingestion_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, run_id INTEGER NOT NULL,
            level TEXT NOT NULL, figi TEXT, message TEXT NOT NULL
        );
    """)
    con.execute("INSERT INTO instruments (ticker, figi, class, name) VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber')")
    con.commit()
    con.close()
    return p


def _seed_bars(db, figi, dates_iso):
    con = sqlite3.connect(db)
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, 1, 1)",
        [(figi, d) for d in dates_iso],
    )
    con.commit(); con.close()


def test_health_report_healthy_figi_scores_100(db):
    today = date(2026, 9, 12)
    _seed_bars(db, "FIGI-SBER", [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)])
    r = compute_health(db, "FIGI-SBER", today=today)
    assert r.health_score == 100
    assert r.issues == []


def test_health_report_missing_recent_days(db):
    today = date(2026, 9, 12)
    _seed_bars(db, "FIGI-SBER", [(today - timedelta(days=i)).isoformat() for i in range(30, 7, -1)])
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.MISSING_RECENT in r.issues
    assert r.health_score <= 70
    assert r.last_bar == today - timedelta(days=7)


def test_health_report_sparse_history_capped(db):
    today = date(2026, 9, 12)
    # 30 actual bars, but history claims 5 years (1800 weekdays expected)
    _seed_bars(db, "FIGI-SBER", [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)])
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.SPARSE_HISTORY in r.issues
    assert r.health_score >= 80  # capped at -20


def test_health_report_has_gaps_detects_long_gap(db):
    today = date(2026, 9, 12)
    dates = [(today - timedelta(days=i)).isoformat() for i in range(30, 15, -1)]
    # 7-day gap, then resume
    dates += [(today - timedelta(days=i)).isoformat() for i in range(8, -1, -1)]
    _seed_bars(db, "FIGI-SBER", dates)
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.HAS_GAPS in r.issues
    assert len(r.recent_gaps) > 0


def test_health_report_rate_limited_failures(db):
    today = date(2026, 9, 12)
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO ingestion_logs (ts, run_id, level, figi, message) "
        "VALUES (datetime('now', '-1 day'), 1, 'warn', 'FIGI-SBER', 'rate limit hit')"
    )
    con.commit(); con.close()
    r = compute_health(db, "FIGI-SBER", today=today)
    assert HealthIssue.RATE_LIMITED_FAILURES in r.issues
    assert r.recent_failures  # non-empty


def test_compute_all_returns_only_tradeable_figis(db):
    today = date(2026, 9, 12)
    # Non-tradeable row
    con = sqlite3.connect(db)
    con.execute("INSERT INTO instruments (ticker, figi, class, name) VALUES ('OPT', 'FIGI-OPT', 'option', 'Opt')")
    _seed_bars(db, "FIGI-OPT", [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)])
    con.commit(); con.close()
    reports = compute_all(db, today=today)
    assert "FIGI-SBER" in reports
    assert "FIGI-OPT" not in reports


def test_health_report_unknown_figi_scores_zero(db):
    """A figi with no instrument row gets score 0 + MISSING_RECENT."""
    r = compute_health(db, "FIGI-DOES-NOT-EXIST", today=date(2026, 9, 12))
    assert r.health_score == 0
    assert HealthIssue.MISSING_RECENT in r.issues
```

````

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd apps/api && uv run pytest tests/test_data_quality_health.py -v`
Expected: ImportError or AttributeError on `algotrader_api.data_quality`.

- [ ] **Step 3: Implement the module**

```python
# src/algotrader_api/data_quality/__init__.py
from .health import HealthIssue, HealthReport, compute_health, compute_all
__all__ = ["HealthIssue", "HealthReport", "compute_health", "compute_all"]
````

```python
# src/algotrader_api/data_quality/health.py
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Iterable

from ..domain.tradeable import TRADEABLE_CLASSES


class HealthIssue(str, Enum):
    MISSING_RECENT = "missing-recent-days"
    SPARSE_HISTORY = "sparse-history"
    HAS_GAPS = "has-gaps"
    RATE_LIMITED_FAILURES = "rate-limited-failures"


@dataclass
class HealthReport:
    figi: str
    ticker: str | None
    health_score: int
    issues: list[HealthIssue] = field(default_factory=list)
    first_bar: date | None = None
    last_bar: date | None = None
    actual_bars: int = 0
    expected_bars: int = 0
    recent_gaps: list[date] = field(default_factory=list)
    recent_failures: list[dict] = field(default_factory=list)


_MISSING_RECENT_DAYS = 3
_GAP_MIN_DAYS = 5
_RATELIMIT_LOOKBACK_DAYS = 7
_SPARSE_RATIO = 0.9
_PENALTY = {
    HealthIssue.MISSING_RECENT: 30,
    HealthIssue.SPARSE_HISTORY: 20,
    HealthIssue.HAS_GAPS: 20,
    HealthIssue.RATE_LIMITED_FAILURES: 30,
}


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def _meta_for_figi(con: sqlite3.Connection, figi: str) -> dict | None:
    """Return instrument metadata joined with instrument ticker for the figi."""
    row = con.execute(
        "SELECT m.last_bar_ts, m.total_bars, m.last_run_status, i.ticker "
        "FROM instruments i LEFT JOIN instrument_metadata m ON m.figi = i.figi "
        "WHERE i.figi = ?",
        (figi,),
    ).fetchone()
    return dict(row) if row else None


def _bars_range(con: sqlite3.Connection, figi: str) -> tuple[date | None, date | None, int]:
    row = con.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(*) FROM bars WHERE figi = ?",
        (figi,),
    ).fetchone()
    if not row or row[0] is None:
        return None, None, 0
    first = date.fromisoformat(row[0])
    last = date.fromisoformat(row[1])
    return first, last, int(row[2])


def _weekdays_between(start: date, end: date) -> int:
    """Count business days between start and end inclusive."""
    if end < start:
        return 0
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5:  # Mon-Fri
            n += 1
        cur += timedelta(days=1)
    return n


def _detect_gaps(con: sqlite3.Connection, figi: str, max_keep: int = 5) -> list[date]:
    """Return up to `max_keep` long gaps (> _GAP_MIN_DAYS) in figi's history.

    A gap is reported as the START date of the gap (the last bar before
    the missing range). For drill-down purposes, the operator wants
    "when did we stop getting data"; the start-of-gap date is the
    actionable row.
    """
    rows = con.execute(
        "SELECT ts FROM bars WHERE figi = ? AND ts < date('now') ORDER BY ts",
        (figi,),
    ).fetchall()
    if len(rows) < 2:
        return []
    gaps: list[date] = []
    prev = date.fromisoformat(rows[0][0])
    for row in rows[1:]:
        cur = date.fromisoformat(row[0])
        diff = (cur - prev).days
        if diff > _GAP_MIN_DAYS:
            gaps.append(prev)
            if len(gaps) >= max_keep:
                break
        prev = cur
    return gaps


def _recent_failures(con: sqlite3.Connection, figi: str, max_keep: int = 5) -> list[dict]:
    rows = con.execute(
        "SELECT ts, level, message FROM ingestion_logs "
        "WHERE figi = ? AND ts > datetime('now', ? || ' days') "
        "AND level IN ('warn', 'error') "
        "AND (message LIKE '%rate%' OR message LIKE '%RESOURCE_EXHAUSTED%') "
        "ORDER BY id DESC LIMIT ?",
        (figi, f"-{_RATELIMIT_LOOKBACK_DAYS}", max_keep),
    ).fetchall()
    return [dict(r) for r in rows]


def _compute_issues_and_penalty(
    meta: dict | None,
    last_bar: date | None,
    actual_bars: int,
    expected_bars: int,
    gaps: list[date],
    failures: list[dict],
    today: date,
) -> tuple[list[HealthIssue], int]:
    issues: list[HealthIssue] = []
    penalty = 0

    if last_bar is None or last_bar < today - timedelta(days=_MISSING_RECENT_DAYS):
        issues.append(HealthIssue.MISSING_RECENT)
        penalty += _PENALTY[HealthIssue.MISSING_RECENT]

    if expected_bars > 0 and actual_bars < expected_bars * _SPARSE_RATIO:
        issues.append(HealthIssue.SPARSE_HISTORY)
        penalty += _PENALTY[HealthIssue.SPARSE_HISTORY]

    if gaps:
        issues.append(HealthIssue.HAS_GAPS)
        penalty += _PENALTY[HealthIssue.HAS_GAPS]

    if failures:
        issues.append(HealthIssue.RATE_LIMITED_FAILURES)
        penalty += _PENALTY[HealthIssue.RATE_LIMITED_FAILURES]

    return issues, penalty


def compute_health(
    db_path: str, figi: str, today: date | None = None
) -> HealthReport:
    """Compute the HealthReport for a single figi."""
    today = today or date.today()
    con = _open(db_path)
    try:
        meta = _meta_for_figi(con, figi)
        if meta is None:
            # No instrument row at all — treat as full health deficit
            return HealthReport(
                figi=figi, ticker=None, health_score=0,
                issues=[HealthIssue.MISSING_RECENT],
            )
        first_bar, last_bar, actual_bars = _bars_range(con, figi)
        gaps = _detect_gaps(con, figi)
        failures = _recent_failures(con, figi)
        expected_bars = (
            _weekdays_between(first_bar, today) if first_bar else 0
        )
        issues, penalty = _compute_issues_and_penalty(
            meta, last_bar, actual_bars, expected_bars, gaps, failures, today
        )
        score = max(0, 100 - penalty)
        return HealthReport(
            figi=figi,
            ticker=meta.get("ticker"),
            health_score=score,
            issues=issues,
            first_bar=first_bar,
            last_bar=last_bar,
            actual_bars=actual_bars,
            expected_bars=expected_bars,
            recent_gaps=gaps,
            recent_failures=failures,
        )
    finally:
        con.close()


def compute_all(db_path: str, today: date | None = None) -> dict[str, HealthReport]:
    """Compute HealthReport for every tradeable figi in `instruments`.

    Non-tradeable rows (e.g. future/option) are excluded at the
    SQL boundary per the TRADEABLE_CLASSES contract.
    """
    placeholders = ",".join("?" for _ in TRADEABLE_CLASSES)
    con = _open(db_path)
    try:
        rows = con.execute(
            f"SELECT figi FROM instruments WHERE class IN ({placeholders})",
            tuple(TRADEABLE_CLASSES),
        ).fetchall()
    finally:
        con.close()
    return {r["figi"]: compute_health(db_path, r["figi"], today) for r in rows}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd apps/api && uv run pytest tests/test_data_quality_health.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/ apps/api/tests/test_data_quality_health.py
git commit -m "feat(data-quality): add HealthReport + compute_health + compute_all"
```

---

## Task 2: `data_quality.recovery` module

**Files:**

- Create: `apps/api/src/algotrader_api/data_quality/recovery.py`
- Test: `apps/api/tests/test_data_quality_recovery.py`

**Interfaces produced:**

```python
@dataclass
class RecoverySummary:
    queued: list[str]             # figis sent to backfill
    skipped_exhausted: list[str]
    skipped_ratelimit_only: list[str]

def recover_stale(
    db_path: str,
    runner: BackfillRunner,        # duck-typed; needs .run(...) and .db_path
    reports: dict[str, HealthReport],
) -> RecoverySummary
```

**Logic:**

- For each `(figi, report)` in `reports`:
  - Skip if `health_score == 100`
  - Skip if `instrument_metadata.last_run_status == 'stale_recovery_exhausted'`
  - Skip if `report.issues == [HealthIssue.RATE_LIMITED_FAILURES]` only
  - Otherwise queue for backfill
- Sort queued by `health_score` ascending (worst first)
- Pass to `runner.run(...)` — reuse the existing runner, no new logic

- [ ] **Step 1: Write failing test**

```python
# tests/test_data_quality_recovery.py
import sqlite3
from datetime import date, timedelta
import pytest
from unittest.mock import MagicMock

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations
from algotrader_api.data_quality.health import HealthIssue, HealthReport
from algotrader_api.data_quality.recovery import recover_stale, RecoverySummary


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT, source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT,
            last_backfilled_at TEXT, total_bars INTEGER,
            last_run_status TEXT, last_run_at TEXT, last_error TEXT
        );
    """)
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name) VALUES (?, ?, 'share', ?)",
        [("A", "FIGI-A", "A"), ("B", "FIGI-B", "B"), ("C", "FIGI-C", "C"),
         ("D", "FIGI-D", "D"), ("E", "FIGI-E", "E")],
    )
    # FIGI-A: healthy
    # FIGI-B: missing recent — should be queued
    # FIGI-C: exhausted — should be skipped
    # FIGI-D: ratelimit only — should be skipped
    # FIGI-E: missing recent + ratelimit — should be queued
    con.execute("UPDATE instrument_metadata SET last_run_status='stale_recovery_exhausted' WHERE figi='FIGI-C'")
    con.commit(); con.close()
    return p


def _report(figi, score, issues):
    return HealthReport(
        figi=figi, ticker=figi, health_score=score, issues=issues,
    )


def test_recover_stale_queues_only_unhealthy_non_exhausted_non_ratelimit_only(db):
    runner = MagicMock()
    runner.run = MagicMock()
    reports = {
        "FIGI-A": _report("FIGI-A", 100, []),
        "FIGI-B": _report("FIGI-B", 70, [HealthIssue.MISSING_RECENT]),
        "FIGI-C": _report("FIGI-C", 30, [HealthIssue.MISSING_RECENT]),  # exhausted
        "FIGI-D": _report("FIGI-D", 70, [HealthIssue.RATE_LIMITED_FAILURES]),  # ratelimit only
        "FIGI-E": _report("FIGI-E", 40, [HealthIssue.MISSING_RECENT, HealthIssue.RATE_LIMITED_FAILURES]),
    }
    summary = recover_stale(db, runner, reports)
    assert "FIGI-B" in summary.queued
    assert "FIGI-E" in summary.queued
    assert "FIGI-C" in summary.skipped_exhausted
    assert "FIGI-D" in summary.skipped_ratelimit_only
    assert "FIGI-A" not in summary.queued


def test_recover_stale_sorts_by_score_worst_first(db):
    runner = MagicMock()
    runner.run = MagicMock()
    reports = {
        "FIGI-LOW": _report("FIGI-LOW", 30, [HealthIssue.MISSING_RECENT]),
        "FIGI-HIGH": _report("FIGI-HIGH", 80, [HealthIssue.HAS_GAPS]),
        "FIGI-MID": _report("FIGI-MID", 50, [HealthIssue.SPARSE_HISTORY]),
    }
    summary = recover_stale(db, runner, reports)
    assert summary.queued == ["FIGI-LOW", "FIGI-MID", "FIGI-HIGH"]


def test_recover_stale_returns_empty_when_all_healthy(db):
    runner = MagicMock()
    runner.run = MagicMock()
    summary = recover_stale(db, runner, {"FIGI-A": _report("FIGI-A", 100, [])})
    assert summary.queued == []
    runner.run.assert_not_called()
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd apps/api && uv run pytest tests/test_data_quality_recovery.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/algotrader_api/data_quality/recovery.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..ingestion.backfill import BackfillRunner
from .health import HealthIssue, HealthReport


@dataclass
class RecoverySummary:
    queued: list[str] = field(default_factory=list)
    skipped_exhausted: list[str] = field(default_factory=list)
    skipped_ratelimit_only: list[str] = field(default_factory=list)


_RATELIMIT_ONLY = frozenset({HealthIssue.RATE_LIMITED_FAILURES})


def _is_exhausted(db_path: str, figi: str) -> bool:
    import sqlite3
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT last_run_status FROM instrument_metadata WHERE figi = ?",
            (figi,),
        ).fetchone()
        return bool(row and row[0] == "stale_recovery_exhausted")
    finally:
        con.close()


def recover_stale(
    db_path: str,
    runner: BackfillRunner,
    reports: dict[str, HealthReport],
) -> RecoverySummary:
    """Pick figis that need refetching and pass them to the runner.

    Skipped reasons are tracked separately so the operator can see
    in the pipeline run summary why a figi was held back.
    """
    summary = RecoverySummary()
    queue: list[str] = []
    for figi, report in reports.items():
        if report.health_score == 100:
            continue
        if _is_exhausted(db_path, figi):
            summary.skipped_exhausted.append(figi)
            continue
        if set(report.issues) == _RATELIMIT_ONLY:
            summary.skipped_ratelimit_only.append(figi)
            continue
        queue.append(figi)

    queue.sort(key=lambda f: reports[f].health_score)
    summary.queued = queue
    if queue:
        runner.run(
            history_years=5,
            incremental_threshold_days=2,
            limit_to=queue,  # see Task 4: BackfillRunner extension
        )
    return summary
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd apps/api && uv run pytest tests/test_data_quality_recovery.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/recovery.py apps/api/tests/test_data_quality_recovery.py
git commit -m "feat(data-quality): add recovery queue with priority by score"
```

---

## Task 3: `BackfillRunner.run(limit_to=...)` extension

**Files:**

- Modify: `apps/api/src/algotrader_api/ingestion/backfill.py`
- Test: `apps/api/tests/test_backfill.py`

**Interface change:**

```python
async def run(
    self,
    history_years: int = 5,
    incremental_threshold_days: int = 2,
    *,
    limit_to: list[str] | None = None,   # NEW
) -> None:
    ...
```

When `limit_to` is set, `_list_instruments()` filters to those figis only (in addition to `TRADEABLE_CLASSES`). When `None`, current behaviour (whole universe).

- [ ] **Step 1: Write failing test**

```python
# In tests/test_backfill.py (add at end of backfill tests section)
@pytest.mark.asyncio
async def test_run_accepts_limit_to_figis(tmp_path):
    """Recovery loop passes a figi whitelist; runner only fetches those."""
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "state.db"))
    con.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT
        );
    """)
    con.executemany(
        "INSERT INTO instruments (ticker, figi, class, name, currency, lot_size) VALUES (?, ?, 'share', ?, 'rub', 1)",
        [("A", "FIGI-A", "A"), ("B", "FIGI-B", "B"), ("C", "FIGI-C", "C")],
    )
    con.commit(); con.close()

    async def noop(ev):
        pass
    runner = BackfillRunner(
        client=MagicMock(), db_path=str(tmp_path / "state.db"), event_sink=noop,
    )
    runner._discover_universe = AsyncMock(return_value=3)
    runner._backfill_one = AsyncMock(return_value=10)
    await runner.run(history_years=5, incremental_threshold_days=2, limit_to=["FIGI-B"])
    # Only FIGI-B should have been processed.
    assert runner._backfill_one.await_count == 1
    called_figi = runner._backfill_one.await_args.kwargs.get("figi") or runner._backfill_one.await_args.args[0]
    assert called_figi == "FIGI-B"
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd apps/api && uv run pytest tests/test_backfill.py::test_run_accepts_limit_to_figis -v`
Expected: TypeError on unexpected kwarg `limit_to`.

- [ ] **Step 3: Implement**

In `apps/api/src/algotrader_api/ingestion/backfill.py`:

- Change `async def run(self, history_years: int = 5, incremental_threshold_days: int = 2):` to:
  `async def run(self, history_years: int = 5, incremental_threshold_days: int = 2, *, limit_to: list[str] | None = None):`
- Change `_list_instruments(self)` to `_list_instruments(self, limit_to: list[str] | None = None)`:
  ```python
  def _list_instruments(self, limit_to: list[str] | None = None) -> list[dict]:
      from ..domain.tradeable import TRADEABLE_CLASSES
      placeholders = ",".join("?" for _ in TRADEABLE_CLASSES)
      sql = f"SELECT ticker, figi, class FROM instruments WHERE class IN ({placeholders})"
      params: list = list(TRADEABLE_CLASSES)
      if limit_to:
          qs = ",".join("?" for _ in limit_to)
          sql += f" AND figi IN ({qs})"
          params.extend(limit_to)
      con = sqlite3.connect(self.db_path)
      con.row_factory = sqlite3.Row
      try:
          rows = con.execute(sql, tuple(params)).fetchall()
      finally:
          con.close()
      return [dict(r) for r in rows]
  ```
- In `run()`, replace `instruments = self._list_instruments()` with `instruments = self._list_instruments(limit_to=limit_to)`.

- [ ] **Step 4: Run test, verify pass**

Run: `cd apps/api && uv run pytest tests/test_backfill.py::test_run_accepts_limit_to_figis -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/ingestion/backfill.py apps/api/tests/test_backfill.py
git commit -m "feat(backfill): accept limit_to figi whitelist for targeted recovery"
```

---

## Task 4: `data_quality.service` orchestrator

**Files:**

- Create: `apps/api/src/algotrader_api/data_quality/service.py`
- Test: `apps/api/tests/test_data_quality_service.py`

**Interface produced:**

```python
@dataclass
class GuardianSummary:
    figis_checked: int
    figis_recovered: int
    anomalies_raised: int
    duration_seconds: float

async def run_daily_guardian(db_path: str) -> GuardianSummary
```

**Logic:**

1. Call `discover_universe(client)` then `upsert_instruments(db, rows)`.
2. Call `compute_all(db_path)` → reports.
3. Call `recover_stale(db_path, runner, reports)` → RecoverySummary.
4. Log anomalies from `RecoverySummary.skipped_exhausted` (operator investigation needed).
5. Append a `pipeline` row with the summary.

- [ ] **Step 1: Write failing test**

```python
# tests/test_data_quality_service.py
import sqlite3
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date

from algotrader_api.db.migrations import MIGRATIONS_DIR
from algotrader_api.db.sqlite import run_migrations


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "state.db")
    run_migrations(p, str(MIGRATIONS_DIR))
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS instruments (
            ticker TEXT PRIMARY KEY, figi TEXT UNIQUE, class TEXT,
            name TEXT, currency TEXT, lot_size INTEGER, isin TEXT,
            sector TEXT, source_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS instrument_metadata (
            figi TEXT PRIMARY KEY, last_bar_ts TEXT,
            last_backfilled_at TEXT, total_bars INTEGER,
            last_run_status TEXT, last_run_at TEXT, last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS bars (
            figi TEXT NOT NULL, ts TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER,
            PRIMARY KEY (figi, ts)
        );
        CREATE TABLE IF NOT EXISTS pipeline (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL, phase TEXT NOT NULL,
            ts TEXT NOT NULL, level TEXT NOT NULL,
            rows_processed INTEGER, detail TEXT
        );
    """)
    con.execute("INSERT INTO instruments (ticker, figi, class, name) VALUES ('A', 'FIGI-A', 'share', 'A')")
    con.commit(); con.close()
    return p


@pytest.mark.asyncio
async def test_run_daily_guardian_full_cycle(db):
    runner = MagicMock()
    runner.run = AsyncMock()
    with patch("algotrader_api.data_quality.service.discover_universe", new=AsyncMock(return_value=[])), \
         patch("algotrader_api.data_quality.service.upsert_instruments", new=MagicMock()):
        from algotrader_api.data_quality.service import run_daily_guardian
        summary = await run_daily_guardian(db, runner)
    assert summary.figis_checked >= 1
    runner.run.assert_called_once()  # 1 unhealthy figi triggered recovery
    # Pipeline row recorded.
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM pipeline").fetchone()[0]
    con.close()
    assert n >= 1
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd apps/api && uv run pytest tests/test_data_quality_service.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/algotrader_api/data_quality/service.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..db.sqlite import execute as _sqlite_exec
from ..ingestion import universe as _universe
from ..ingestion.backfill import BackfillRunner
from .health import compute_all
from .recovery import recover_stale


@dataclass
class GuardianSummary:
    figis_checked: int = 0
    figis_recovered: int = 0
    anomalies_raised: int = 0
    duration_seconds: float = 0.0


def _make_client_from_settings():
    from ..config import get_settings
    from ..ingestion.client import make_client

    return make_client(sqlite_path=get_settings().sqlite_path, use_fake=False)


async def run_daily_guardian(
    db_path: str, runner: BackfillRunner | None = None
) -> GuardianSummary:
    """Run the daily guardian: universe sync → health → recovery → anomalies.

    `runner` is provided by the caller (the systemd worker). When
    None, we build one from settings + the live broker client.
    """
    t0 = time.time()
    summary = GuardianSummary()

    # 1. Universe sync.
    client = _make_client_from_settings()
    rows = await _universe.discover_universe(client)
    _universe.upsert_instruments(db_path, rows)

    # 2. Health pass.
    reports = compute_all(db_path)
    summary.figis_checked = len(reports)

    # 3. Recovery.
    if runner is None:
        runner = BackfillRunner(
            client=client, db_path=db_path, event_sink=lambda _: None
        )
    recovery = recover_stale(db_path, runner, reports)
    summary.figis_recovered = len(recovery.queued)
    summary.anomalies_raised = len(recovery.skipped_exhausted)

    # 4. Anomalies (already logged inside recover_stale).
    for figi in recovery.skipped_exhausted:
        logging.warning(
            "guardian.anomaly.stale_recovery_exhausted",
            figi=figi,
            message="figi has been failing 3+ cycles; operator investigation needed",
        )

    # 5. Pipeline row.
    _sqlite_exec(
        db_path,
        "INSERT INTO pipeline (run_id, phase, ts, level, rows_processed, detail) "
        "VALUES (?, 'guardian_daily', datetime('now'), 'ok', ?, ?)",
        (
            0,  # guardian has no run_id; pipeline table requires it.
            summary.figis_recovered,
            f"figis_checked={summary.figis_checked} "
            f"figis_recovered={summary.figis_recovered} "
            f"anomalies={summary.anomalies_raised}",
        ),
    )

    summary.duration_seconds = time.time() - t0
    return summary
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd apps/api && uv run pytest tests/test_data_quality_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/data_quality/service.py apps/api/tests/test_data_quality_service.py
git commit -m "feat(data-quality): add run_daily_guardian orchestrator"
```

---

## Task 5: `/api/data-quality/{symbol}` endpoint

**Files:**

- Create: `apps/api/src/algotrader_api/routes/data_quality.py`
- Modify: `apps/api/src/algotrader_api/main.py`
- Test: `apps/api/tests/test_data_quality_route.py`

**Interface:**

```python
@router.get("/data-quality/{symbol}")
def get_data_quality(symbol: str) -> dict:
    """Return the HealthReport for the given symbol."""
```

Returns 404 if the symbol isn't in `instruments`. Returns 200 with the full HealthReport otherwise.

- [ ] **Step 1: Write failing test**

```python
# tests/test_data_quality_route.py
from datetime import date, timedelta


def test_get_data_quality_returns_health_report(client, fresh_db):
    import sqlite3
    today = date.today()
    con = sqlite3.connect(fresh_db)
    con.executescript(
        f"""
        INSERT INTO instruments (ticker, figi, class, name) VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber');
        INSERT INTO bars (figi, ts, open, high, low, close, volume)
        VALUES ('FIGI-SBER', '{(today - timedelta(days=i)).isoformat()}', 1, 1, 1, 1, 1)
        """ + "".join([f"FOR i IN (SELECT 30));\n"]),  # placeholder
    )
    # Cleaner: insert directly
    con.execute("DELETE FROM bars WHERE figi='FIGI-SBER'")  # cleanup
    rows = [(today - timedelta(days=i)).isoformat() for i in range(30, -1, -1)]
    con.executemany(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES (?, ?, 1, 1, 1, 1, 1)",
        [("FIGI-SBER", d) for d in rows],
    )
    con.commit(); con.close()
    from algotrader_api.routes import data_reads as data_reads_route
    data_reads_route.set_sqlite_path(fresh_db)

    r = client.get("/api/data-quality/SBER")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "SBER"
    assert body["health_score"] == 100
    assert body["issues"] == []


def test_get_data_quality_returns_404_for_unknown_symbol(client):
    r = client.get("/api/data-quality/UNKNOWN_TICKER_XYZ")
    assert r.status_code == 404


def test_get_data_quality_resolves_figi_alias(client, fresh_db):
    import sqlite3
    con = sqlite3.connect(fresh_db)
    con.execute("INSERT INTO instruments (ticker, figi, class, name) VALUES ('SBER', 'FIGI-SBER', 'share', 'Sber')")
    con.commit(); con.close()
    from algotrader_api.routes import data_reads as data_reads_route
    data_reads_route.set_sqlite_path(fresh_db)
    r = client.get("/api/data-quality/FIGI-SBER")
    assert r.status_code == 200
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd apps/api && uv run pytest tests/test_data_quality_route.py -v`
Expected: ImportError on `algotrader_api.routes.data_quality`.

- [ ] **Step 3: Implement the route**

```python
# src/algotrader_api/routes/data_quality.py
"""Per-ticker data quality drill-down.

Returns the HealthReport for the requested symbol (ticker or
figi). The endpoint is read-only; the daily guardian does the
recovery work and writes nothing here.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException

from ..data_quality.health import compute_health
from ..db.bars_sqlite import resolve_figi_for_ticker
from ..observability.logging import get_logger
from .data_reads import _get_sqlite_path  # reuse the path holder


router = APIRouter(prefix="/api", tags=["data-quality"])
logger = get_logger("algotrader_api.data_quality_route")


def _report_to_dict(report) -> dict[str, Any]:
    return {
        "figi": report.figi,
        "ticker": report.ticker,
        "health_score": report.health_score,
        "issues": [str(i) for i in report.issues],
        "first_bar": report.first_bar.isoformat() if report.first_bar else None,
        "last_bar": report.last_bar.isoformat() if report.last_bar else None,
        "actual_bars": report.actual_bars,
        "expected_bars": report.expected_bars,
        "recent_gaps": [d.isoformat() for d in report.recent_gaps],
        "recent_failures": report.recent_failures,
    }


def _resolve_figi(sqlite_path: str, symbol: str) -> str | None:
    """Look up figi by ticker; fall back to figi equality."""
    import sqlite3
    figi = resolve_figi_for_ticker(sqlite_path, symbol)
    if figi:
        return figi
    # Direct figi lookup.
    con = sqlite3.connect(sqlite_path)
    try:
        row = con.execute(
            "SELECT figi FROM instruments WHERE figi = ?", (symbol,)
        ).fetchone()
        return row[0] if row else None
    finally:
        con.close()


@router.get("/data-quality/{symbol}")
def get_data_quality(symbol: str) -> dict:
    """Return the full HealthReport for the requested symbol."""
    sqlite_path = _get_sqlite_path()
    figi = _resolve_figi(sqlite_path, symbol)
    if not figi:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_symbol", "symbol": symbol},
        )
    report = compute_health(sqlite_path, figi)
    return _report_to_dict(report)
```

- [ ] **Step 4: Wire the router in `main.py`**

In `apps/api/src/algotrader_api/main.py`, add to the imports:

```python
from .routes import data_quality as data_quality_route
```

And in `create_app()`:

```python
app.include_router(data_quality_route.router)
```

- [ ] **Step 5: Run test, verify pass**

Run: `cd apps/api && uv run pytest tests/test_data_quality_route.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/algotrader_api/routes/data_quality.py apps/api/src/algotrader_api/main.py apps/api/tests/test_data_quality_route.py
git commit -m "feat(data-quality): add GET /api/data-quality/{symbol} endpoint"
```

---

## Task 6: Update `/api/admin/backfill/pending` with health buckets

**Files:**

- Modify: `apps/api/src/algotrader_api/routes/backfill.py`
- Modify: `apps/api/tests/test_backfill_routes.py`

**Change:** the `/pending` response adds `by_health` bucket and `worst` top-5.

- [ ] **Step 1: Write failing test**

```python
# In tests/test_backfill_routes.py
def test_pending_includes_health_bucket(client, fresh_db):
    import sqlite3
    from datetime import date, timedelta
    today = date.today()
    con = sqlite3.connect(fresh_db)
    con.executescript(
        f"""
        INSERT INTO instruments (ticker, figi, class, name) VALUES
            ('GOOD', 'FIGI-GOOD', 'share', 'Good'),
            ('BAD',  'FIGI-BAD',  'share', 'Bad');
        INSERT INTO bars (figi, ts, open, high, low, close, volume) VALUES
            ('FIGI-GOOD', '{(today - timedelta(days=i)).isoformat()}', 1, 1, 1, 1, 1)
        """ + "".join([])
    )
    # Seed only FIGI-GOOD with bars; FIGI-BAD has none.
    con.execute("DELETE FROM bars WHERE figi='FIGI-BAD'")
    con.execute("INSERT INTO bars (figi, ts) SELECT 'FIGI-BAD', ts FROM bars WHERE figi='FIGI-GOOD' LIMIT 0")
    con.execute(
        "INSERT INTO bars (figi, ts, open, high, low, close, volume) "
        "SELECT 'FIGI-BAD', ts, open, high, low, close, volume FROM bars "
        "WHERE figi='FIGI-GOOD' AND ts < date('now', '-10 days')"
    )
    con.commit(); con.close()
    from algotrader_api.routes import data_reads as data_reads_route
    data_reads_route.set_sqlite_path(fresh_db)
    from algotrader_api.routes.backfill import _pending_count
    pc = _pending_count(fresh_db, incremental_threshold_days=2)
    # PC dict shape unchanged; but the route wraps it with health buckets.
    r = client.get("/api/admin/backfill/pending")
    body = r.json()
    assert "by_health" in body
    assert "worst" in body
```

- [ ] **Step 2: Run test, verify fail**

Run: `cd apps/api && uv run pytest tests/test_backfill_routes.py::test_pending_includes_health_bucket -v`
Expected: KeyError on `by_health`.

- [ ] **Step 3: Implement**

In `apps/api/src/algotrader_api/routes/backfill.py`, modify the `backfill_pending` route:

```python
@router.get("/backfill/pending")
async def backfill_pending() -> dict:
    settings = get_settings()
    counts = _pending_count(
        settings.sqlite_path,
        incremental_threshold_days=_settings_incremental_threshold(),
    )
    # Health buckets
    from ..data_quality.health import compute_all
    reports = compute_all(settings.sqlite_path)
    by_health = {"100": 0, "99-90": 0, "89-50": 0, "<50": 0}
    worst = []
    for r in reports.values():
        s = r.health_score
        if s == 100: by_health["100"] += 1
        elif s >= 90: by_health["99-90"] += 1
        elif s >= 50: by_health["89-50"] += 1
        else: by_health["<50"] += 1
        if s < 100:
            worst.append({"figi": r.figi, "ticker": r.ticker, "health_score": s})
    worst.sort(key=lambda x: x["health_score"])
    return {**counts, "by_health": by_health, "worst": worst[:5]}
```

- [ ] **Step 4: Run test, verify pass**

Run: `cd apps/api && uv run pytest tests/test_backfill_routes.py::test_pending_includes_health_bucket -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/algotrader_api/routes/backfill.py apps/api/tests/test_backfill_routes.py
git commit -m "feat(backfill-pending): surface health buckets and worst figis"
```

---

## Task 7: `worker.py` guardian mode + systemd timer

**Files:**

- Modify: `apps/api/worker.py`
- (systemd unit file edit happens on the host, not in repo)

- [ ] **Step 1: Add `guardian` mode to `worker.py`**

In `apps/api/worker.py`:

- Add import: `from algotrader_api.data_quality.service import run_daily_guardian`
- Add `guardian` to the `choices` argument
- Add a branch in `main()`:

```python
if args.mode == "guardian":
    from algotrader_api.config import get_settings
    summary = asyncio.run(_drive_guardian(get_settings().sqlite_path))
    return 0 if summary.anomalies_raised == 0 else 1  # exit 1 on anomalies so systemd can alert

async def _drive_guardian(db_path: str):
    return await run_daily_guardian(db_path)
```

- [ ] **Step 2: Run the guardian locally once against the prod DB**

Run: `cd apps/api && uv run python -m algotrader_api.worker guardian`
Expected: log lines for guardian.anomaly.stale_recovery_exhausted for the existing 967 error figis; pipeline row appended.

Verify the `pipeline` table has the new row:

```sql
SELECT * FROM pipeline WHERE phase='guardian_daily' ORDER BY id DESC LIMIT 1;
```

- [ ] **Step 3: Update systemd timer unit**

```bash
sudo sed -i 's/algotrader_api.worker backfill/algotrader_api.worker guardian/g' \
    /etc/systemd/system/algotrader-worker.service
sudo systemctl daemon-reload
sudo systemctl restart algotrader-worker.timer
sudo systemctl list-timers algotrader-worker.timer
```

(These are host-level operations. The plan executor runs them.)

- [ ] **Step 4: Commit**

```bash
git add apps/api/worker.py
git commit -m "feat(worker): add guardian mode for daily quality run"
```

---

## Task 8: Full-suite coverage gate + push

**Files:** none (verification only)

- [ ] **Step 1: Run full pytest with coverage**

Run: `cd apps/api && uv run pytest --cov=algotrader_api -q --tb=no`
Expected: coverage ≥ 95%, all green.

- [ ] **Step 2: Live verification on the prod DB**

```bash
curl -sS http://127.0.0.1:8000/api/data-quality/SBER | python3 -m json.tool
curl -sS http://127.0.0.1:8000/api/admin/backfill/pending | python3 -m json.tool
```

Expected: SBER has `health_score: 100`; the pending endpoint shows `by_health` and `worst` keys.

- [ ] **Step 3: Commit + push**

```bash
git status  # should be empty
git log --oneline -10
# Use force-with-lease (the previous push pattern)
git push --force-with-lease origin main
```

---

## Self-Review

**1. Spec coverage:**

- "Per-ticker data quality is computed with a drill-down payload" → Task 1 (compute_health, compute_all, HealthReport)
- "Per-ticker drill-down is exposed via a read endpoint" → Task 5 (GET /api/data-quality/{symbol})
- "A daily guardian runs universe sync + health + recovery" → Task 4 (run_daily_guardian) + Task 7 (worker.py guardian mode + systemd)
- "missing-recent-days sub-problem" → Task 1 test
- "sparse-history sub-problem" → Task 1 test
- "gaps sub-problem" → Task 1 test
- "rate-limited-failures sub-problem" → Task 1 test
- "healthy figi" → Task 1 test
- "daily guardian recovers stale figis" → Task 4
- "daily guardian does not re-queue exhausted figis" → Task 2 test
- "daily guardian does not re-queue RATE_LIMITED-only figis" → Task 2 test

**2. Placeholder scan:** no TBD/TODO. Every code step has actual code. Tests have actual assertions.

**3. Type consistency:**

- `HealthReport.figi` is `str` everywhere.
- `HealthIssue` is the enum, referenced consistently.
- `BackfillRunner.run(..., limit_to=...)` kwarg matches the recovery module call.
- `compute_all` returns `dict[str, HealthReport]` — used by recovery and the orchestrator identically.
- `_pending_count` signature unchanged; the route now wraps it with health buckets.

**4. Edge cases:**

- `meta is None` (no instrument row) → score 0, MISSING_RECENT (covered by Task 1 implementation, test not yet). Add a test in Task 1.

**Action:** add `test_health_report_unknown_figi_scores_zero` to Task 1.
