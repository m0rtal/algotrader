#!/usr/bin/env bash
# Daily refresh pipeline — invoked by cron at 20:00 UTC daily as a dead man's
# switch for the algotrader-moex-backfill supervisor.
#
# Why this is a "switch" and not a primary runner:
# The supervisor-managed worker (see scripts/algotrader-supervisor.sh) is the
# authoritative source of refresh activity — it runs the worker in a tight
# loop with a watchdog and write-locks the SQLite WAL. A naive `worker.py daily`
# invocation in this script would race the supervisor worker for the same
# database, producing intermittent database is locked errors and a watchdog
# false-positive that kills the supervisor's worker.
#
# Historical evidence (daily-refresh.log on 2026-09-22):
#   17:00:26 universe_sync ok (3802 instruments)
#   17:00:26 backfill_moex phase_start
#   (continues into Tinkoff gRPC retry loop)
#   ... killed (supervisor watchdog saw "bars_growth=0" for >10 min and
#        SIGKILLed the cron worker, not the supervising one).
#
# Approach: bail out early if supervisor is healthy; if supervisor is missing,
# bring it up under the same supervisor wrapper so future cron invocations
# also no-op until it dies again.

set -euo pipefail

SUPERVISOR="/home/hermes/algotrader/scripts/algotrader-supervisor.sh"
APPS_API="/home/hermes/algotrader/apps/api"
VENV_PY="${APPS_API}/.venv/bin/python"

# Sanity: supervisor script must exist.
if [[ ! -x "$SUPERVISOR" ]]; then
    echo "[cron_daily_refresh] FATAL: supervisor not found at $SUPERVISOR" >&2
    exit 1
fi

# Healthy state: supervisor-managed worker already running. Exit 0 quietly.
if pgrep -f 'worker.py daily' >/dev/null 2>&1; then
    echo "[cron_daily_refresh] $(date -Iseconds) supervisor-managed worker is alive; nothing to do."
    exit 0
fi

# Degenerate state: no worker running. Either the supervisor is wedged, the
# container rebooted while cron was offline (no @reboot hit), or the chain
# crashed without being respawned by the watchdog. Spawn the supervisor so
# the chain comes back; the supervisor itself will run daily ad infinitum
# (no need for this cron to do anything else).
echo "[cron_daily_refresh] $(date -Iseconds) no worker detected; launching supervisor as dead-man's-switch."
nohup bash "$SUPERVISOR" \
    algotrader-moex-backfill \
    "$VENV_PY" worker.py daily "$APPS_API" \
    >> /home/hermes/.hermes/logs/algotrader-moex-backfill.log 2>&1 &
disown
exit 0
