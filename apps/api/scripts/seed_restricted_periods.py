# apps/api/scripts/seed_restricted_periods.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the restricted-periods seed.

Runs the implementation in
`algotrader_api.scripts_import.seed_restricted_periods` so the script
can be invoked as:

    python -m scripts.seed_restricted_periods <db_path>

while tests exercise the same logic via the in-package import path.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from algotrader_api.scripts_import.seed_restricted_periods import (  # noqa: E402
    seed_restricted_periods,
)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m scripts.seed_restricted_periods <db_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    n = seed_restricted_periods(sys.argv[1])
    print(f"Seeded {n} restricted periods")
