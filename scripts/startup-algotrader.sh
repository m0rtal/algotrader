#!/usr/bin/env bash
# startup-algotrader.sh — bring up the algotrader stack at session boot.
#
# Why: After container reboot (or hermes-agent restart) nothing auto-respawns.
# This script is registered in the hermes user crontab as @reboot; the cron
# daemon (active system-wide via /usr/lib/systemd/system/cron.service) handles
# the actual triggering.
#
# Idempotent: safe to re-run. Killing pgrep entries before starting avoids
# "address in use" errors when the chain is already alive.

set -u

PROJECT="/home/hermes/algotrader"
LOG_DIR="/home/hermes/.hermes/logs"
mkdir -p "$LOG_DIR"

PY="$PROJECT/apps/api/.venv/bin/python"
UVICORN="$PROJECT/apps/api/.venv/bin/uvicorn"
API_DIR="$PROJECT/apps/api"
WEB_DIR="$PROJECT/apps/web"

start_guard() {
    local name="$1"
    if pgrep -f "$name" >/dev/null 2>&1; then
        echo "[startup] $name already running, skipping."
        return 0
    fi
    echo "[startup] starting $name"
}

# ---- 1. supervisor-managed worker (moex-backfill chain) ----
if ! pgrep -f 'worker.py daily' >/dev/null 2>&1; then
    nohup bash "$PROJECT/scripts/algotrader-supervisor.sh" \
        algotrader-moex-backfill \
        "$PY" worker.py daily "$API_DIR" \
        >> "$LOG_DIR/algotrader-moex-backfill.log" 2>&1 &
    disown
    echo "[startup] worker launched (pid $!)"
else
    echo "[startup] worker already running"
fi

# Give worker 8s to release DB write-lock for migration phase.
sleep 8

# ---- 2. uvicorn (FastAPI backend on :8000) ----
if ! pgrep -f 'uvicorn algotrader_api.main' >/dev/null 2>&1; then
    cd "$API_DIR" || { echo "[startup] cd failed: $API_DIR"; exit 1; }
    nohup "$UVICORN" algotrader_api.main:app \
        --host 0.0.0.0 --port 8000 \
        >> "$LOG_DIR/algotrader-uvicorn.log" 2>&1 &
    disown
    cd - >/dev/null
    echo "[startup] uvicorn launched (pid $!)"
else
    echo "[startup] uvicorn already running"
fi

# ---- 3. vite (web frontend on :5173) ----
if ! pgrep -f 'vite --host 0.0.0.0' >/dev/null 2>&1; then
    cd "$WEB_DIR" || { echo "[startup] cd failed: $WEB_DIR"; exit 1; }
    nohup /usr/bin/npm run dev -- --host 0.0.0.0 \
        >> "$LOG_DIR/algotrader-vite.log" 2>&1 &
    disown
    cd - >/dev/null
    echo "[startup] vite launched (pid $!)"
else
    echo "[startup] vite already running"
fi

# ---- 4. health check (slow because uvicorn needs ~5s to bind) ----
for i in 1 2 3 4 5 6 7 8 9 10; do
    sleep 3
    if curl -m 3 -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "[startup] backend healthy after ${i} tries ($((i*3))s)"
        break
    fi
done

if curl -m 3 -sf http://127.0.0.1:5173/ >/dev/null 2>&1; then
    echo "[startup] vite healthy"
fi

echo "[startup] done at $(date -Iseconds)"
