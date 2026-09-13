# scripts/data/

Operator-facing data files for `scripts/import_*.py` entry points.

The canonical MOEX holidays JSON lives alongside its implementation at
`src/algotrader_api/scripts_import/data/moex_holidays.json` so the
in-package import path and the `python -m scripts.import_moex_holidays`
wrapper share one source of truth.
