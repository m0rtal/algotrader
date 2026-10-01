#!/usr/bin/env bash
# Algotrader moex-backfill worker, started with a clean Python environment.
#
# The host shell (set up by hermes-agent) exports a PYTHONPATH that points at
# hermes' own python3.14 site-packages. The algotrader .venv is python3.11
# and shadows those packages — when PYTHONPATH stays in scope, algotrader
# worker.py imports hermes' pydantic_core (which has no compiled extension
# for 3.11) and exits rc=1 immediately. The watchdog then restarts in an
# infinite failure loop.
#
# This wrapper explicitly unsets PYTHONPATH and re-execs the existing
# algotrader-supervisor.sh so the worker sees only its .venv's site-packages.
#
# Usage:
#   bash scripts/algotrader-moex-supervisor-clean.sh
#
# Logs go to the same place as algotrader-supervisor.sh.
set -u

unset PYTHONPATH
unset PYTHONHOME

exec /home/hermes/algotrader/scripts/algotrader-supervisor.sh \
    algotrader-moex-backfill \
    /home/hermes/algotrader/apps/api/.venv/bin/python \
    worker.py \
    daily \
    first \
    /home/hermes/algotrader/apps/api
