"""Tests for the unified UI snapshot: a single JSON response that the
frontend loads in one round-trip to render the Data tab KPI block and
the data-quality summary.

The endpoint exists to take the UI render budget from ~0.75s (current
/api/tickers warm) to under 100ms by reading a precomputed JSON file
off disk instead of executing per-request SQLite + Python queries.

Why a JSON file rather than an in-memory cache: the budget must hold
across backend restarts and across cold browser tabs. The snapshot is
the single source of truth for "what does the UI show right now" —
the slow-path aggregation rebuilds it on worker-driven bar inserts and
on the daily corporate-actions sweep.

The staleness invariant is enforced both ways:
  - Write side: ``maybe_refresh`` skips recomputation if the snapshot
    was written within ``REFRESH_BUDGET_S`` (5s in steady state). Hot
    figis only refresh once per budget window — a worker that writes
    bar inserts every 5–10s triggers one recompute per cycle.
  - Read side: ``/api/tickers`` and ``/admin/backfill/pending``
    always recompute when the cached snapshot is older than
    ``SNAPSHOT_STALENESS_BUDGET_S`` (60s). This is the upper bound on
    staleness the UI sees if the worker stalls, and it doubles as the
    automatic-rebuild path on cold-start, fresh DB.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from algotrader_api.ui_snapshot import (
    REFRESH_BUDGET_S,
    UiSnapshot,
    compute_snapshot,
    get_snapshot_path,
    maybe_refresh,
)


def test_compute_snapshot_shape_matches_ui_consumers(fresh_db: str) -> None:
    """Frontend reads three things from the snapshot:
      tickers (array of per-figi dicts, same shape as /api/tickers)
      pendingCounts (the four-bucket dict from /admin/backfill/pending)
      health (per-class report summary)

    All three must be present as top-level keys so the frontend can
    call useUiSnapshot() once instead of fetching each endpoint
    separately (the whole point of the snapshot).
    """
    snapshot = compute_snapshot(fresh_db)

    assert isinstance(snapshot, UiSnapshot)
    assert isinstance(snapshot.tickers, list)
    assert isinstance(snapshot.pending_counts, dict)
    assert "new" in snapshot.pending_counts
    assert "stale" in snapshot.pending_counts
    assert "up_to_date" in snapshot.pending_counts
    assert "total" in snapshot.pending_counts
    assert "by_health" in snapshot.pending_counts
    assert isinstance(snapshot.pending_counts["by_health"], dict)
    assert "worst" in snapshot.pending_counts
    assert isinstance(snapshot.pending_counts["worst"], list)
    assert snapshot.generated_at > 0
    assert hasattr(snapshot, "version")


def test_maybe_refresh_writes_atomically(fresh_db: str) -> None:
    """maybe_refresh must write a complete file (atomic rename) so a
    frontend request that lands during the rewrite never sees a half-
    written JSON document — that would crash JSON.parse on the client.
    """
    snapshot_path = get_snapshot_path(fresh_db)
    assert snapshot_path.name == "ui_snapshot.json"
    assert snapshot_path.parent == Path(fresh_db).parent

    assert not snapshot_path.exists()
    maybe_refresh(sqlite_path=fresh_db, force=True)
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["version"] >= 1
    assert "tickers" in payload
    assert "pending_counts" in payload


def test_maybe_refresh_skips_when_budget_unspent(fresh_db: str) -> None:
    """If the snapshot was refreshed less than REFRESH_BUDGET_S ago
    and the input parameters haven't changed, maybe_refresh must
    return without recomputing. The version field is unchanged.

    This protects against hot endpoints (e.g. /api/ui-snapshot called
    from a smoke test every 30s) that could otherwise trigger
    compute_snapshot on every hit, which on prod takes ~700ms.
    """
    snapshot_path = get_snapshot_path(fresh_db)
    maybe_refresh(sqlite_path=fresh_db, force=True)
    first_mtime = snapshot_path.stat().st_mtime_ns
    first_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))

    maybe_refresh(sqlite_path=fresh_db)
    assert snapshot_path.stat().st_mtime_ns == first_mtime
    assert (
        json.loads(snapshot_path.read_text(encoding="utf-8"))["version"]
        == first_payload["version"]
    )

    maybe_refresh(sqlite_path=fresh_db, force=True)
    second_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert second_payload["version"] == first_payload["version"] + 1


def test_round_trip_is_lossless(fresh_db: str) -> None:
    """What we write must be what we read. Regression guard against
    accidental type coercion (datetime → str, Decimal → float, etc).
    """
    maybe_refresh(sqlite_path=fresh_db, force=True)
    raw = json.loads(get_snapshot_path(fresh_db).read_text())
    snap = UiSnapshot.from_dict(raw)
    assert snap.generated_at == raw["generated_at"]
    assert snap.version == raw["version"]
    assert snap.pending_counts == raw["pending_counts"]
    assert len(snap.tickers) == len(raw["tickers"])
