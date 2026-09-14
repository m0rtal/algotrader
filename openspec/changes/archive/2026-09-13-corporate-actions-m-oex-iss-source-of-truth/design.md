# Design: corporate-actions-m-oex-iss-source-of-truth

## Stack

- **Python 3.11** stdlib only for the new code (`sqlite3`, `json`,
  `urllib.request`, `dataclasses`, `datetime`).
- **`t_tech.invest`** (the renamed `tinkoff-investments`) for the
  dividends fetcher. Already installed (was `tinkoff.invest` in the
  previous change, now corrected).
- **MOEX ISS REST API** — `https://iss.moex.com/iss/securities/{secid}.json`
  (face_value / lot snapshot) and `.../dividends.json` (historical
  dividends). Free, no auth, rate-limited to ~50 req/min per IP.

## Layout

```
algotrader/
├── apps/api/
│   ├── src/algotrader_api/
│   │   ├── db/
│   │   │   └── migrations/
│   │   │       └── 009_corporate_actions_source.sql          (NEW)
│   │   └── scripts_import/
│   │       ├── import_corporate_actions_splits.py           (NEW: snapshot + diff)
│   │       ├── import_corporate_actions_moex.py              (MODIFY: source field)
│   │       ├── import_corporate_actions_tinkoff.py           (MODIFY: live async + source)
│   │       └── import_corporate_actions_common.py            (UNCHANGED: shared writer)
│   ├── scripts/
│   │   ├── import_corporate_actions_splits.py               (NEW: operator wrapper)
│   │   ├── import_corporate_actions_moex.py                 (MODIFY: source comment)
│   │   └── import_corporate_actions_tinkoff.py              (MODIFY: env-var gated)
│   └── tests/
│       ├── test_corporate_actions_source_backfill.py         (NEW)
│       ├── test_corporate_actions_splits.py                  (NEW)
│       └── test_corporate_actions_moex.py                    (MODIFY: source field)
├── apps/api/scripts/data/
│   └── splits_curated.json                                  (DELETE)
└── openspec/
    └── specs/
        └── dev-workflow/
            └── spec.md                                      (MODIFY: +1 Requirement)
```

## Components

### 1. Migration `009_corporate_actions_source.sql`

```sql
-- Adds the source column and indexes it for fast filtering.
ALTER TABLE corporate_actions ADD COLUMN source TEXT;
CREATE INDEX IF NOT EXISTS idx_corporate_actions_source
    ON corporate_actions(source);
```

Backfill is data-side, not migration-side. The backfill script (next
section) handles both backfilling and the deletion of unverifiable
rows.

### 2. Backfill + cleanup (one-off script, not in cron path)

`apps/api/scripts/migrate_corporate_actions_source.py`:

```python
"""One-time migration: backfill `source` from existing `note` prefixes,
delete rows that can't be traced to a verifiable source."""
from __future__ import annotations

import sqlite3
from datetime import datetime


def backfill_source_and_cleanup(db_path: str) -> dict:
    """Return a stats dict: {backfilled: N, deleted: M, kept: K}."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # 1. Backfill `source` from `note` prefix
    cur.execute("""
        UPDATE corporate_actions
        SET source = 'tinkoff:dividends'
        WHERE source IS NULL AND note LIKE 'tinkoff:%'
    """)
    tinkoff_backfilled = cur.rowcount
    cur.execute("""
        UPDATE corporate_actions
        SET source = 'moex:iss:dividends'
        WHERE source IS NULL AND note LIKE 'moex:iss'
    """)
    moex_backfilled = cur.rowcount

    # 2. Delete rows that can't be traced
    cur.execute("""
        DELETE FROM corporate_actions WHERE source IS NULL
    """)
    deleted = cur.rowcount

    con.commit()
    cur.execute("SELECT COUNT(*) FROM corporate_actions")
    kept = cur.fetchone()[0]
    con.close()
    return {
        "tinkoff_backfilled": tinkoff_backfilled,
        "moex_backfilled": moex_backfilled,
        "deleted": deleted,
        "kept": kept,
    }
```

This is a one-off. It runs **once** during Task 5 (live verify). Not
on the cron path.

### 3. Snapshot table (`009b_instruments_snapshot.sql`, second file in
the same migration series)

```sql
CREATE TABLE IF NOT EXISTS instruments_snapshot (
    figi           TEXT NOT NULL,
    face_value     REAL NOT NULL,
    lot            INTEGER NOT NULL,
    observed_at    TEXT NOT NULL,
    PRIMARY KEY (figi, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_instruments_snapshot_figi
    ON instruments_snapshot(figi, observed_at);
```

The snapshot is a rolling log of MOEX ISS observations. The
split-detection step reads the **two most recent** snapshots per figi
and diffs `face_value` / `lot`. Older snapshots are kept for
audit but not used for diffing.

### 4. New `import_corporate_actions_splits.py`

Two modes:
- `mode="snapshot"` (default): fetch `/iss/securities/{secid}.json`
  for every tradeable figi, write one row per figi to
  `instruments_snapshot`. No `corporate_actions` writes happen.
- `mode="detect"`: for every tradeable figi, find the two most recent
  snapshots. If `face_value` differs, compute
  `factor = face_value_new / face_value_old` and write one
  `split` row to `corporate_actions` with
  `source = 'moex:iss:split-diff:<snapshot_old_ts>:<snapshot_new_ts>'`.

```python
"""Detect and record stock splits by diffing MOEX ISS face_value/lot
snapshots over time.

Two modes:
  python -m scripts.import_corporate_actions_splits <db> snapshot
  python -m scripts.import_corporate_actions_splits <db> detect

Snapshot mode runs frequently (e.g. weekly); detect mode runs less
often (e.g. after at least two snapshots exist for a figi).
"""
from __future__ import annotations

import argparse
import sqlite3
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from ..db.sqlite import get_connection


def _figi_to_secid(db_path: str, figi: str) -> Optional[str]:
    con = sqlite3.connect(db_path)
    cur = con.execute("SELECT ticker FROM instruments WHERE figi = ?", (figi,))
    row = cur.fetchone()
    con.close()
    return row[0] if row else None


def _http_get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "algotrader-snapshot/1.0",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        import json as _json
        return _json.loads(resp.read())


def _tradeable_figis(db_path: str) -> list[tuple[str, str]]:
    """Return list of (figi, secid) for tradeable instruments."""
    con = sqlite3.connect(db_path)
    cur = con.execute("""
        SELECT figi, ticker FROM instruments
        WHERE class IN ('share', 'etf', 'bond')
    """)
    rows = cur.fetchall()
    con.close()
    return [(f, s) for f, s in rows if s]


def _fetch_face_value_lot(secid: str) -> tuple[Optional[float], Optional[int]]:
    """Read FACE_VALUE and LOT from /iss/securities/{secid}.json."""
    url = f"https://iss.moex.com/iss/securities/{secid}.json?iss.meta=off"
    payload = _http_get_json(url)
    # MOEX ISS returns multiple blocks; the securities.description block
    # has the FACE_VALUE / LOT columns. We accept the structure is
    # version-dependent and tolerate missing fields.
    for block_name, block in payload.items():
        if not isinstance(block, dict):
            continue
        cols = block.get("columns", [])
        rows = block.get("data", [])
        if "FACE_VALUE" in cols and "LOT" in cols:
            for row in rows:
                kv = dict(zip(cols, row))
                fv = kv.get("FACE_VALUE")
                lot = kv.get("LOT")
                if fv is not None and lot is not None:
                    try:
                        return float(fv), int(lot)
                    except (TypeError, ValueError):
                        pass
    return None, None


def write_snapshot(db_path: str) -> int:
    """Read MOEX ISS for every tradeable figi and write a snapshot."""
    observed_at = datetime.now(timezone.utc).isoformat()
    pairs = _tradeable_figis(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    n_written = 0
    for figi, secid in pairs:
        fv, lot = _fetch_face_value_lot(secid)
        if fv is None or lot is None:
            continue
        cur.execute("""
            INSERT INTO instruments_snapshot(figi, face_value, lot, observed_at)
            VALUES (?, ?, ?, ?)
        """, (figi, fv, lot, observed_at))
        n_written += 1
    con.commit()
    con.close()
    return n_written


def detect_splits(db_path: str) -> int:
    """Find splits by diffing the two most recent snapshots per figi."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("""
        SELECT figi, face_value, lot, observed_at FROM instruments_snapshot
        WHERE (figi, observed_at) IN (
            SELECT figi, MAX(observed_at) FROM instruments_snapshot GROUP BY figi
        )
        ORDER BY figi
    """)
    latest = {r[0]: (r[1], r[2], r[3]) for r in cur}

    cur.execute("""
        SELECT s.figi, s.face_value, s.lot, s.observed_at
        FROM instruments_snapshot s
        JOIN (
            SELECT figi, MAX(observed_at) AS m
            FROM instruments_snapshot GROUP BY figi
        ) latest ON latest.figi = s.figi
        JOIN instruments_snapshot prev
            ON prev.figi = s.figi
            AND prev.observed_at < s.observed_at
            AND prev.observed_at = (
                SELECT MAX(p.observed_at) FROM instruments_snapshot p
                WHERE p.figi = s.figi AND p.observed_at < s.observed_at
            )
    """)
    n_detected = 0
    for figi, new_fv, new_lot, new_ts in cur:
        prev = latest.get(figi)
        if prev is None:
            continue
        prev_fv, prev_lot, prev_ts = prev
        if new_fv == prev_fv and new_lot == prev_lot:
            continue
        factor = new_fv / prev_fv if prev_fv else 1.0
        source = f"moex:iss:split-diff:{prev_ts}:{new_ts}"
        try:
            cur.execute("""
                INSERT OR IGNORE INTO corporate_actions
                    (figi, action_type, ex_date, factor, cash_amount, note, source)
                VALUES (?, 'split', ?, ?, NULL, ?, ?)
            """, (figi, new_ts[:10], factor, f"face_value {prev_fv} -> {new_fv}, lot {prev_lot} -> {new_lot}", source))
            if cur.rowcount > 0:
                n_detected += 1
        except sqlite3.IntegrityError:
            pass  # PK conflict — already recorded
    con.commit()
    con.close()
    return n_detected


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db_path")
    ap.add_argument("mode", choices=["snapshot", "detect"])
    args = ap.parse_args()
    if args.mode == "snapshot":
        n = write_snapshot(args.db_path)
        print(f"Wrote {n} snapshots")
    else:
        n = detect_splits(args.db_path)
        print(f"Detected {n} new splits")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
```

### 5. `import_corporate_actions_moex.py` modifications

The existing script already writes rows via the common helper. Add
the `source` field:

```python
# Change every CorporateActionRow construction in this file:
CorporateActionRow(
    figi=...,
    action_type="dividend",
    ex_date=...,
    factor=1.0,
    cash_amount=v,
    note="moex:iss:dividends",
    source="moex:iss:dividends",  # NEW
)
```

Same for the operator wrapper's docstring: mention `source` is set
automatically.

### 6. `import_corporate_actions_tinkoff.py` modifications

The previous change shipped a runtime stub. Replace it with the
async SDK pattern used in `ingestion/real_client.py`. Read the broker
token from `secrets`. Add the `source` field. Gate behind an env var
to keep it out of the cron path until live-tested:

```python
import os
from ..db.secrets import get_broker_token
from ..config import Settings

ENABLED = os.environ.get("ALGOTRADER_TINKOFF_ENABLE") == "1"


async def _fetch_dividends_tinkoff(figi: str, from_, to):
    if not ENABLED:  # pragma: no cover
        return []
    import importlib
    sdk = importlib.import_module("t_tech.invest")
    token = get_broker_token(Settings().sqlite_path) or ""
    async with sdk.AsyncClient(token) as client:
        resp = await client.instruments.get_dividends(figi=figi, from_=from_, to=to)
        for ev in getattr(resp, "events", []):
            yield ev
```

The full async rewrite is out of scope for this change but is the
follow-up Task 8 in the plan.

### 7. OpenSpec delta

Append to `dev-workflow` capability (which already covers the
branch-only rule; the source-audit rule is governance):

```markdown
## ADDED Requirements

### Requirement: Every corporate_actions row carries a verifiable source

The system SHALL persist a `source` column on the `corporate_actions`
table. Every row inserted via an importer MUST set `source` to a
machine-verifiable string that includes:

- The provider identifier (`tinkoff`, `moex:iss`, or `manual`)
- The data type (`dividends`, `split-diff`)
- For `split-diff`: the two snapshot timestamps used to compute the
  factor (so the factor is reproducible from the snapshots)

Rows WITHOUT a `source` value SHALL be removed by a one-time
backfill migration. No importer SHALL write a row with `source =
NULL`.

#### Scenario: dividends importer tags every row

- GIVEN `python -m scripts.import_corporate_actions_moex state.db`
- WHEN a dividend event is fetched from MOEX ISS
- THEN the resulting `corporate_actions` row has
  `source = 'moex:iss:dividends'`

#### Scenario: split-detection diff tags with snapshot timestamps

- GIVEN two snapshots for figi X with face_value 1.0 (ts=T1) and 50.0
  (ts=T2)
- WHEN the diff detector runs
- THEN the resulting `corporate_actions` row has
  `source = 'moex:iss:split-diff:T1:T2'` and `factor = 50.0`

#### Scenario: rows without source are removed

- GIVEN the `corporate_actions` table contains rows with `source IS NULL`
- WHEN the one-off backfill script runs
- THEN those rows are deleted
- AND every remaining row has a non-null `source`
```

## Data flow

```
operator → python -m scripts.import_corporate_actions_splits state.db snapshot
   → for figi in tradeable:
       GET iss.moex.com/iss/securities/{secid}.json
       → face_value, lot
   → INSERT INTO instruments_snapshot
   (no corporate_actions writes)

[wait N days/weeks]

operator → python -m scripts.import_corporate_actions_splits state.db detect
   → for figi:
       prev_snapshot = latest except newest
       cur_snapshot  = newest
       if face_value changed:
           factor = cur.fv / prev.fv
           INSERT INTO corporate_actions (..., source='moex:iss:split-diff:T1:T2')

operator → python -m scripts.import_corporate_actions_moex state.db
   → for figi in tradeable:
       GET iss.moex.com/iss/securities/{secid}/dividends.json
       → INSERT INTO corporate_actions (..., source='moex:iss:dividends')

[after live test]

operator → export ALGOTRADER_TINKOFF_ENABLE=1
operator → python -m scripts.import_corporate_actions_tinkoff state.db
   → for figi in tradeable:
       async get_dividends via Tinkoff SDK
       → INSERT INTO corporate_actions (..., source='tinkoff:dividends')
```

## Tests

- `test_corporate_actions_source_backfill.py` — backfill script:
  verifies rows with `tinkoff:` prefix get `source='tinkoff:dividends'`,
  rows with `moex:iss` get `source='moex:iss:dividends'`, rows with no
  prefix get deleted.
- `test_corporate_actions_splits.py` — split-detection:
  - inserts two snapshots with different face_values, verifies split row
    appears with correct factor and `source` containing both timestamps.
  - inserts two snapshots with identical face_values, verifies NO split
    row is written.
  - snapshot write mode: mocked HTTP returns a known payload, verifies
    the right `(figi, face_value, lot)` rows land in
    `instruments_snapshot`.
- `test_corporate_actions_moex.py` — verify every dividend row now
  carries `source='moex:iss:dividends'`.
- `test_corporate_actions_tinkoff.py` — verify every row carries
  `source='tinkoff:dividends'` (already passes; minor update).

## Rollback

- The `source` column is nullable. Drop it via a reverse migration.
- The `instruments_snapshot` table can be dropped without consequence.
- `splits_curated.json` was already deleted in this change; restoring
  it via `git revert` is technically possible but **should not be done**.

## Operator notes

- Run `python -m scripts.import_corporate_actions_splits state.db
  snapshot` weekly (Sunday 02:00 UTC is a reasonable default).
- Run `python -m scripts.import_corporate_actions_splits state.db
  detect` **only after** at least two snapshots exist for the figis
  you care about. Running detect on the first snapshot ever produces
  zero splits (correct).
- Run `python -m scripts.import_corporate_actions_moex state.db`
  weekly as well. Idempotent.
- Enable Tinkoff only after the operator has run a manual live test:
  `ALGOTRADER_TINKOFF_ENABLE=1 python -m scripts.import_corporate_actions_tinkoff state.db`
  and confirmed the broker accepts the calls.
