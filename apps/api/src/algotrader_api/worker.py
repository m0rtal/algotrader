"""Re-export shim so ``from algotrader_api.worker import ...`` works.

The real worker module lives at ``apps/api/worker.py`` (it's invoked by
the supervisor as ``python worker.py daily`` from cwd=apps/api). Tests
that prefer the package-qualified import can use this shim; the runtime
contract and behaviour are identical because the module object is the
same.
"""
from __future__ import annotations

import importlib.util
import os
import sys


def _load_worker_module():
    """Load apps/api/worker.py as the ``algotrader_api.worker`` module."""
    here = os.path.dirname(os.path.abspath(__file__))
    apps_api = os.path.abspath(os.path.join(here, "..", ".."))
    worker_path = os.path.join(apps_api, "worker.py")
    spec = importlib.util.spec_from_file_location(
        "algotrader_api.worker", worker_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load worker spec from {worker_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_module = _load_worker_module()

# Re-export the public attributes the anchor test and any consumer
# rely on. Anything else is accessible via the module object itself.
_DAILY_CHAIN_PHASES = _module._DAILY_CHAIN_PHASES
_DAILY_CHAIN_FIRST_PHASES = _module._DAILY_CHAIN_FIRST_PHASES
_DAILY_CHAIN_DERIVED_PHASES = _module._DAILY_CHAIN_DERIVED_PHASES
_select_subset = _module._select_subset
_selected_phases = _module._selected_phases
run_daily_chain = _module.run_daily_chain
_STEP_FUNCS = _module._STEP_FUNCS
_CRITICAL_PHASES = _module._CRITICAL_PHASES