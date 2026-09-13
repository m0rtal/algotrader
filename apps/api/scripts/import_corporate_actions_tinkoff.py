# apps/api/scripts/import_corporate_actions_tinkoff.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the Tinkoff dividends fetcher.

Runs the implementation in
`algotrader_api.scripts_import.import_corporate_actions_tinkoff` so the
script can be invoked as:

    python -m scripts.import_corporate_actions_tinkoff <db_path>

while tests exercise the same logic via the in-package import path.
"""
from __future__ import annotations

import os
import sys

# Make `algotrader_api` importable when this file is run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from algotrader_api.scripts_import.import_corporate_actions_tinkoff import (  # noqa: E402
    import_corporate_actions_tinkoff,
)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.import_corporate_actions_tinkoff <db_path>",
              file=sys.stderr)
        sys.exit(2)
    n = import_corporate_actions_tinkoff(sys.argv[1])
    print(f"Imported {n} Tinkoff dividends")