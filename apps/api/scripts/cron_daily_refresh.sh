#!/usr/bin/env bash
# Daily refresh pipeline — runs the full chain (migrations → universe
# sync → daily backfill → corporate actions → dividends → guardian).
# Invoked by the system cron at 20:00 UTC daily.

set -euo pipefail

APPS_API="/home/hermes/algotrader/apps/api"
VENV_PY="${APPS_API}/.venv/bin/python"

cd "${APPS_API}"

exec "${VENV_PY}" worker.py daily
