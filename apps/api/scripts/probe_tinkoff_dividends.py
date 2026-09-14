# apps/api/scripts/probe_tinkoff_dividends.py
#!/usr/bin/env python3
"""Thin operator-facing wrapper for the Task-1 Tinkoff GetDividends
shape probe.

Runs the implementation in
`algotrader_api.scripts_import.probe_tinkoff_dividends` so the script
can be invoked as:

    PYTHONPATH=src ./.venv/bin/python \\
        -m algotrader_api.scripts_import.probe_tinkoff_dividends

while tests exercise the same logic via the in-package import path.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import asyncio

from algotrader_api.scripts_import.probe_tinkoff_dividends import (  # noqa: E402
    _run,
)

if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))