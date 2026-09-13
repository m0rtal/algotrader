"""Operator wrapper: derive historical splits from local bars.

Usage:
    uv run python -m scripts.derive_splits data/state.db

The DB must already have the `bars` and `corporate_actions` tables
(migration 005/007). This script:

1. Iterates all figis with bars.
2. Optionally fetches current `face_value` from MOEX ISS.
3. Detects transitions with ratio <= 0.5 OR >= 2.0.
4. Writes ``CorporateActionRow`` rows with source
   ``derived:bars+facevalue:<ts1>:<ts2>``.

Idempotent — re-runs are no-ops (PK collision).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the in-package module importable when running as a script.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from algotrader_api.scripts_import import derive_splits  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive historical split factors from local bars."
    )
    parser.add_argument("db_path", type=Path, help="Path to state.db")
    parser.add_argument(
        "--no-face-value",
        action="store_true",
        help="Skip the MOEX ISS face_value lookup (faster, no audit cross-check).",
    )
    args = parser.parse_args()

    face_values = None
    if not args.no_face_value:
        print("Fetching face_values from MOEX ISS (skip with --no-face-value)…")
        # Build figi → secid map from the instruments table.
        import sqlite3

        conn = sqlite3.connect(args.db_path)
        rows = conn.execute(
            "SELECT DISTINCT b.figi, i.secid FROM bars b "
            "JOIN instruments i ON i.figi = b.figi "
            "WHERE i.secid IS NOT NULL"
        ).fetchall()
        conn.close()
        figis_to_secids = {figi: secid for figi, secid in rows}
        face_values = derive_splits.lookup_face_values(figis_to_secids)
        print(f"  resolved {len(face_values)} face_values for {len(figis_to_secids)} figis")

    written = derive_splits.run_derivation(
        str(args.db_path), face_values=face_values
    )
    print(f"Wrote {written} split rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
