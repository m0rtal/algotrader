#!/usr/bin/env python3
"""Operator shim: fetch dividends from Tinkoff investAPI."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from algotrader_api.scripts_import.import_dividends_tinkoff import (  # noqa: E402
    fetch_and_persist,
    main,
)

if __name__ == "__main__":
    sys.exit(main())
