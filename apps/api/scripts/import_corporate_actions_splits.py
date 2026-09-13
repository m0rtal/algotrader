# apps/api/scripts/import_corporate_actions_splits.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the MOEX ISS split detector.

Runs the implementation in
`algotrader_api.scripts_import.import_corporate_actions_splits` so the
script can be invoked as:

    python -m scripts.import_corporate_actions_splits <db_path> [--mode snapshot|detect|both]

while tests exercise the same logic via the in-package import path.

The corporate-actions cron invokes this wrapper with the default
`--mode both` once per day at 20:30 UTC.
"""
from __future__ import annotations

import os
import sys

# Make `algotrader_api` importable when this file is run as a script.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from algotrader_api.scripts_import.import_corporate_actions_splits import (  # noqa: E402
    _main,
)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: python -m scripts.import_corporate_actions_splits "
            "<db_path> [--mode snapshot|detect|both]",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(_main(sys.argv[1:]))