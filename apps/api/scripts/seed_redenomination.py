# apps/api/scripts/seed_redenomination.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the 1998 redenomination seed.

Runs the implementation in
`algotrader_api.scripts_import.seed_redenomination` so the script
can be invoked as:

    python -m scripts.seed_redenomination <db_path>

while tests exercise the same logic via the in-package import path.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from algotrader_api.scripts_import.seed_redenomination import (  # noqa: E402
    seed_redenomination,
)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m scripts.seed_redenomination <db_path>",
            file=sys.stderr,
        )
        sys.exit(2)
    n = seed_redenomination(sys.argv[1])
    print(f"Seeded 1998 redenomination for {n} pre-1998 figis")
