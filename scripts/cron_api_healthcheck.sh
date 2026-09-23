#!/usr/bin/env bash
# cron_api_healthcheck.sh — backstop for the api-supervisor.
#
# Why this exists (PR #121): the api-supervisor's in-process HTTP
# watchdog polls /health every 10s and SIGKILLs uvicorn on 3
# consecutive failures. But if the supervisor itself dies (OOM,
# kernel OOM-kill, accidental pkill), the watchdog dies with it and
# nothing restarts uvicorn. This 5-minute cron is the second line of
# defence: if /health is unreachable AND no api-supervisor is alive,
# relaunch via startup-algotrader.sh.
#
# Why not just rely on @reboot startup: that fires once per host
# boot. The supervisor can die long after that — and indeed did, on
# 2026-09-23 ~15:42 MSK, leaving uvicorn dead for 3.5 hours.

set -u

BIND="${ALGOTRADER_API_BIND:-0.0.0.0:8000}"
PORT="${BIND##*:}"
HEALTH_URL="http://127.0.0.1:${PORT}/health"
LOG="/home/hermes/.hermes/logs/algotrader-api-cron.log"

mkdir -p "$(dirname "$LOG")"
TS="$(date -Iseconds)"

# Healthy: at least one api-supervisor is running AND /health responds.
if pgrep -f 'algotrader-api-supervisor' >/dev/null 2>&1; then
    if curl -m 3 -sf "$HEALTH_URL" >/dev/null 2>&1; then
        echo "[$TS] api-supervisor alive and /health OK; nothing to do." >> "$LOG"
        exit 0
    fi
    # Supervisor alive but unhealthy. The in-process watchdog will
    # handle this within 30s; don't double-trigger.
    echo "[$TS] api-supervisor alive but /health failing; letting in-process watchdog handle it." >> "$LOG"
    exit 0
fi

# Supervisor dead. Try to bring it back up.
echo "[$TS] FATAL: api-supervisor not running. Relaunching via startup-algotrader.sh." >> "$LOG"

# Use the dedicated supervisor script (matches what @reboot uses).
nohup bash /home/hermes/algotrader/scripts/algotrader-api-supervisor.sh \
    >> /home/hermes/.hermes/logs/algotrader-api.log 2>&1 &
disown
echo "[$TS] relaunched pid=$!" >> "$LOG"
