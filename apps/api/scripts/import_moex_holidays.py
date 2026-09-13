# apps/api/scripts/import_moex_holidays.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the MOEX holidays import.

Runs the implementation in `algotrader_api.scripts_import.import_moex_holidays`
so the script can be invoked as:

    python -m scripts.import_moex_holidays <db_path>

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

from algotrader_api.scripts_import.import_moex_holidays import (  # noqa: E402
    import_moex_holidays,
)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.import_moex_holidays <db_path>", file=sys.stderr)
        sys.exit(2)
    n = import_moex_holidays(sys.argv[1])
    print(f"Imported {n} MOEX holidays")
