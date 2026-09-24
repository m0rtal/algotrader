#!/usr/bin/env bash
# Daily refresh pipeline — invoked by cron at 20:00 UTC daily as a dead man's
# switch for the algotrader-moex-backfill and algotrader-derived supervisors.
#
# Why this is a "switch" and not a primary runner:
# The supervisor-managed workers (see scripts/algotrader-supervisor.sh) are
# the authoritative source of refresh activity — each one runs its worker in
# a tight loop with a watchdog and write-locks the SQLite WAL. A naive
# `worker.py daily` invocation in this script would race the supervisor
# worker for the same database, producing intermittent database is locked
# errors and a watchdog false-positive that kills the supervisor's worker.
#
# Historical evidence (daily-refresh.log on 2026-09-22):
#   17:00:26 universe_sync ok (3802 instruments)
#   17:00:26 backfill_moex phase_start
#   (continues into Tinkoff gRPC retry loop)
#   ... killed (supervisor watchdog saw "bars_growth=0" for >10 min and
#        SIGKILLed the cron worker, not the supervising one).
#
# Approach: bail out early if supervisors are healthy; if any supervisor is
# missing, bring it up under the same supervisor wrapper so future cron
# invocations also no-op until it dies again.

set -euo pipefail

SUPERVISOR="/home/hermes/algotrader/scripts/algotrader-supervisor.sh"
APPS_API="/home/hermes/algotrader/apps/api"
VENV_PY="${APPS_API}/.venv/bin/python"

# Sanity: supervisor script must exist.
if [[ ! -x "$SUPERVISOR" ]]; then
    echo "[cron_daily_refresh] FATAL: supervisor not found at $SUPERVISOR" >&2
    exit 1
fi

# Helper: launch the given supervisor slot if its worker is not alive.
# Mirrors the historical nohup pattern used for algotrader-moex-backfill;
# added (ml-data-readiness PR-2, 2026-09-24) a parallel dead-man block for
# algotrader-derived so the corporate_actions / dividends / freshness_check
# / guardian phases keep running even when the first subset is wedged.
_launch_if_dead() {
    local slot="$1"
    local subset_arg="$2"  # "" = legacy default (no 3rd argv), "derived" for the derived subset
    local logfile="/home/hermes/.hermes/logs/${slot}.log"
    # Pattern must match how the worker is actually invoked. With an
    # empty subset_arg we look for ``worker.py daily`` (no trailing
    # token); with ``derived`` we look for ``worker.py daily derived``.
    local pattern="worker.py daily"
    if [[ -n "$subset_arg" ]]; then
        pattern="worker.py daily ${subset_arg}"
    fi

    if pgrep -f "$pattern" >/dev/null 2>&1; then
        echo "[cron_daily_refresh] $(date -Iseconds) ${slot} worker is alive; nothing to do."
        return 0
    fi

    echo "[cron_daily_refresh] $(date -Iseconds) no ${slot} worker detected; launching supervisor as dead-man's-switch."
    nohup bash "$SUPERVISOR" \
        "$slot" \
        "$VENV_PY" worker.py daily ${subset_arg} "$APPS_API" \
        >> "$logfile" 2>&1 &
    disown
}

# Healthy state: both supervisor-managed workers already running. Exit 0 quietly.
if pgrep -f 'worker.py daily' >/dev/null 2>&1; then
    echo "[cron_daily_refresh] $(date -Iseconds) supervisor-managed workers are alive; nothing to do."
    exit 0
fi

# Degenerate state: no worker running. Either the supervisors are wedged,
# the container rebooted while cron was offline (no @reboot hit), or the
# chain crashed without being respawned by the watchdog. Spawn BOTH
# supervisor slots so the chain comes back; the supervisors themselves
# will run daily ad infinitum (no need for this cron to do anything else).
# ml-data-readiness PR-2: spawn the derived slot too so corporate_actions
# and dividends don't block behind a stalled backfill_moex.
_launch_if_dead "algotrader-moex-backfill" ""
_launch_if_dead "algotrader-derived" "derived"
exit 0
