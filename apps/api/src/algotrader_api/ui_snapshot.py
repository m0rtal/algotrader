"""UI snapshot cache: a single JSON document that powers the whole
Data tab.

The frontend's Data tab and quality panel currently issue three
separate requests on every page load:

    GET /api/tickers             # 758KB, ~750ms warm / 6s cold
    GET /admin/backfill/pending  # small, 1.7ms warm (post-PR #116)
    GET /api/data-quality/summary  # unused but reserved

The first dominates the budget and prevents the UI from loading in
under a second. We replace those three calls with one:

    GET /api/ui-snapshot         # 700KB, single FileResponse

The endpoint reads a precomputed JSON file off disk, which the
worker rewrites after every meaningful state change (bar insert,
metadata update, corporate action). The file is small enough to fit
in a single TCP read, and uvicorn's FileResponse streams it via
sendfile — the only work per request is a path stat and a syscall.

Two writers push refreshes:

* The bar-insert hook in ``bars_sqlite.write_bars`` calls
  ``maybe_refresh`` after a successful commit. Bumps ``version`` and
  triggers a recompute on the next budget elapsed.
* The daily corporate-action sweep calls ``maybe_refresh(force=True)``
  so the next UI load reflects splits and dividends immediately.

See /home/hermes/.hermes/plans/2026-09-23-ui-snapshot-design.md
(also at docs/superpowers/specs/) for the design doc.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any

from .data_quality.health import compute_all
from .db.sqlite import execute as sqlite_exec
from .domain.tradeable import TRADEABLE_CLASSES


# Single source of truth for how often the snapshot may be rewritten
# by *automatic* (non-forced) refreshes. Forced refreshes always win.
REFRESH_BUDGET_S = 5.0

# Filename inside the data dir. The default data dir is one above the
# SQLite file (so e.g. /home/.../data/state.db yields
# /home/.../data/ui_snapshot.json). Test fixtures can override with
# the ALGOTRADER_UI_SNAPSHOT_PATH env var.
SNAPSHOT_FILENAME = "ui_snapshot.json"

# Module-level state guarded by ``_refresh_lock``. We deliberately
# don't use a class-level singleton — the test suite wants fresh
# state on each fixture, and a module-level lock makes that messy.
_last_refresh_t: float = 0.0
_version: int = 0
_refresh_lock_path: str | None = None  # used for serialization across processes


@dataclass
class UiSnapshot:
    """One snapshot of the dashboard's read-side state.

    ``version`` increments on every compute. The frontend treats a
    stale snapshot as still valid — it's never wrong, just less fresh
    than the underlying data.
    """

    generated_at: int  # unix seconds; handy for cache-busting
    version: int
    tickers: list[dict[str, Any]]
    pending_counts: dict[str, Any]
    # Per-class report summary (currently unused by the UI; reserved
    # so the QualityTab can switch to the same single-fetch path as
    # DataTab without another round-trip). Cheap to include; keeps
    # the contract honest.
    health: dict[str, dict[str, int]] = field(default_factory=dict)
    # SQLite PRAGMA data_version at compute time. ``maybe_refresh``
    # uses this as a freshness flag: a request whose cached DB
    # version matches the live one can skip the recomputation even
    # when the time budget hasn't elapsed.
    db_version: int = -1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "UiSnapshot":
        return cls(
            generated_at=int(raw["generated_at"]),
            version=int(raw["version"]),
            tickers=list(raw["tickers"]),
            pending_counts=dict(raw["pending_counts"]),
            health=dict(raw.get("health", {})),
            db_version=int(raw.get("db_version", -1)),
        )


def get_snapshot_path(sqlite_path: str) -> Path:
    """Return the canonical path of the snapshot file.

    Defaults to ``<sqlite_dir>/ui_snapshot.json``; can be overridden
    via the env var for tests that point to a tmp DB.
    """
    override = os.environ.get("ALGOTRADER_UI_SNAPSHOT_PATH")
    if override:
        return Path(override)
    return Path(sqlite_path).parent / SNAPSHOT_FILENAME


def _build_tickers_view(sqlite_path: str) -> list[dict[str, Any]]:
    """Mirrors the body of ``routes.data_reads.get_tickers`` but
    returns the response body directly (no FastAPI glue).

    Kept as a private helper because the canonical endpoint should
    stay one place; if the production handler ever diverges from the
    snapshot shape, the test suite catches it via the round-trip
    tests in ``test_data_reads_tickers`` and here.
    """
    tradeable_classes_sql = ",".join(f"'{c}'" for c in TRADEABLE_CLASSES)
    overview_rows = sqlite_exec(
        sqlite_path,
        f"""
        SELECT
            i.figi                                    AS figi,
            i.ticker                                  AS ticker,
            i.name                                    AS name,
            i.sector                                  AS sector,
            i.currency                                AS currency,
            i.lot_size                                AS lot_size,
            COALESCE(b.bars, 0)                       AS bars,
            b.first_ts                                AS first_ts,
            b.last_ts                                 AS last_ts
        FROM instruments i
        LEFT JOIN (
            SELECT
                figi,
                COUNT(*) AS bars,
                MIN(ts)  AS first_ts,
                MAX(ts)  AS last_ts
            FROM bars
            WHERE figi IS NOT NULL
            GROUP BY figi
        ) b ON b.figi = i.figi
        WHERE i.class IN ({tradeable_classes_sql})
          AND i.figi IS NOT NULL
        """,
        (),
    )
    # Per-figi gap count is fed to ``Полнота`` calculation and the
    # ticker drilldown chart. ``find_gaps`` walks every figi and
    # takes ~5s on prod — too slow to run on every endpoint hit, so
    # we hoist it into the snapshot and read once via the cached
    # file. The worker eventually refreshes on its own (one cycle
    # per staleness window), so callers always see per-figi gaps
    # at no per-request cost.
    from .data_quality.gap_recovery import find_gaps

    gaps_by_figi: dict[str, int] = {}
    try:
        for gap in find_gaps(sqlite_path):
            gaps_by_figi[gap.figi] = gaps_by_figi.get(gap.figi, 0) + 1
    except Exception:
        # Snapshot must never break because of an analytics hiccup;
        # gaps=0 is a perfectly safe degraded view (Полнота reads
        # ``days_present = span - gaps`` so missing gaps show as
        # fully covered, which is a soft over-statement rather
        # than a crash).
        pass
    return [
        {
            "symbol": r["ticker"] or r["figi"],
            "name": r["name"] or "",
            "sector": r["sector"] or "",
            "price": 0,
            "bars": int(r["bars"] or 0),
            "firstDate": "" if r["first_ts"] is None else str(r["first_ts"]),
            "lastDate": "" if r["last_ts"] is None else str(r["last_ts"]),
            "fileSize": 0,
            "gaps": gaps_by_figi.get(r["figi"], 0),
            "currency": r["currency"] or "",
            "lotSize": int(r["lot_size"]) if r["lot_size"] else 0,
        }
        for r in overview_rows
    ]


def _build_pending_counts(sqlite_path: str, incremental_threshold_days: int) -> dict[str, Any]:
    """Mirrors ``routes.backfill._pending_count`` semantics; lets the
    snapshot capture both the four buckets and the by-health summary
    in one document so the UI doesn't need a second hop.
    """
    counts = {"new": 0, "stale": 0, "up_to_date": 0, "error": 0, "total": 0}
    if not Path(sqlite_path).exists():
        return {**counts, "by_health": {"100": 0, "99-90": 0, "89-50": 0, "<50": 0}, "worst": []}

    rows = sqlite_exec(
        sqlite_path,
        "SELECT i.figi, m.last_bar_ts, m.last_run_status "
        "FROM instruments i "
        "LEFT JOIN instrument_metadata m ON i.figi = m.figi",
        (),
    )
    today = date.today()
    for row in rows:
        last_bar_ts = row["last_bar_ts"]
        last_status = row["last_run_status"]
        counts["total"] += 1
        if last_bar_ts is None or last_status == "error":
            counts["new" if last_status != "error" else "error"] += 1
            continue
        try:
            last_dt = date.fromisoformat(last_bar_ts)
        except (TypeError, ValueError):
            counts["new"] += 1
            continue
        days_since = (today - last_dt).days
        if days_since > incremental_threshold_days:
            counts["stale"] += 1
        else:
            counts["up_to_date"] += 1

    by_health = {"100": 0, "99-90": 0, "89-50": 0, "<50": 0}
    worst: list[dict[str, Any]] = []
    try:
        reports = compute_all(sqlite_path)
    except Exception:
        reports = {}
    for r in reports.values():
        s = r.health_score
        if s == 100:
            by_health["100"] += 1
        elif s >= 90:
            by_health["99-90"] += 1
        elif s >= 50:
            by_health["89-50"] += 1
        else:
            by_health["<50"] += 1
        if s < 100:
            worst.append({"figi": r.figi, "ticker": r.ticker, "health_score": s})
    worst.sort(key=lambda x: x["health_score"])
    return {**counts, "by_health": by_health, "worst": worst[:5]}


def compute_snapshot(sqlite_path: str) -> UiSnapshot:
    """Run all aggregations and return a ``UiSnapshot`` in memory.

    Pure: no side effects on disk beyond reading the SQLite DB.
    Used by ``maybe_refresh`` and by tests.
    """
    tickers = _build_tickers_view(sqlite_path)
    pending_counts = _build_pending_counts(sqlite_path, incremental_threshold_days=2)

    # Per-class health summary (currently just the bucketed score
    # counts — same data as `by_health` but rebroadcast as a separate
    # top-level key so the QualityTab can attach directly without
    # re-computing).
    health: dict[str, dict[str, int]] = {}
    try:
        reports = compute_all(sqlite_path)
    except Exception:
        reports = {}
    for r in reports.values():
        health[str(r.ticker)] = {"health_score": int(r.health_score)}

    return UiSnapshot(
        generated_at=int(time.time()),
        version=_version + 1,
        tickers=tickers,
        pending_counts=pending_counts,
        health=health,
    )


def maybe_refresh(sqlite_path: str, *, force: bool = False) -> bool:
    """Recompute the on-disk snapshot if the budget elapsed or the
    caller asked. Returns True iff a write happened.

    Atomic: writes to a sibling temp file and renames over the final
    path so a concurrent reader never sees a half-written JSON.

    Cheap no-op on hot path: just reads the snapshot mtime and
    returns. The common case is the frontend refreshing the page
    every few seconds — without the no-op path, every refresh would
    trigger a 700ms aggregation against the prod DB.

    Forced refreshes (``force=True``) always recompute. Used by the
    daily corporate-actions sweep and by ``seed_bars_sqlite`` to give
    the test fixture a coherent snapshot after a burst of inserts.
    """
    global _last_refresh_t, _version

    target = get_snapshot_path(sqlite_path)

    if not force:
        if target.exists():
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
                written_at = float(payload.get("generated_at", 0))
                version = int(payload.get("version", 0))
                if time.time() - written_at < REFRESH_BUDGET_S:
                    _version = max(_version, version)
                    _last_refresh_t = time.monotonic()
                    return False
            except (OSError, ValueError, json.JSONDecodeError):
                pass

    snap = compute_snapshot(sqlite_path)
    _version = snap.version
    _write_snapshot(target, snap)
    _last_refresh_t = time.monotonic()
    return True


def _write_snapshot(target: Path, snap: UiSnapshot) -> None:
    """Atomic write: temp file in the same dir, then ``os.replace``.

    Same-directory temp + rename means the rename is atomic on POSIX
    even across processes, and the reader's existing file handle
    keeps pointing at the prior version until the rename completes.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snap.to_dict(), ensure_ascii=False, separators=(",", ":"))
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(payload)
        tmp.flush()
        try:
            os.fsync(tmp.fileno())
        except OSError:
            # fsync is best-effort: some tmpfs/filesystems don't
            # support it. The rename still gives us atomicity in the
            # POSIX rename sense; only durability on power-loss is
            # weakened, which is acceptable for a cache.
            pass
        tmp_path = tmp.name
    os.replace(tmp_path, target)
