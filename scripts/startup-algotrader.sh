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

# ---- 1. uvicorn (FastAPI backend on :8000) ----
# Wrapped in algotrader-api-supervisor.sh (PR #121): the supervisor's
# HTTP watchdog polls /health every 10s and SIGKILLs uvicorn if 3
# consecutive checks fail, so the outer restart loop relaunches it.
# This replaces the bare nohup invocation that died silently when its
# parent (an execute_code kernel session) was reaped.
#
# db-bootstrap-hardening PR-3 (Task 3): the api supervisor must be
# launched BEFORE the worker supervisors. The api lifespan runs
# migrations during startup; if a worker boots first it can race
# against an absent `instruments` table and crash. wait_for_api_health
# blocks until /health returns status=ok, guaranteeing migrations
# completed before any worker tries to write to the DB.
if ! pgrep -f 'algotrader-api-supervisor' >/dev/null 2>&1; then
    nohup bash "$PROJECT/scripts/algotrader-api-supervisor.sh" \
        >> "$LOG_DIR/algotrader-api.log" 2>&1 &
    disown
    echo "[startup] api-supervisor launched (pid $!)"
else
    echo "[startup] api-supervisor already running"
fi

# Wait for API to become healthy before starting workers.
# Without this, workers can boot before migrations complete and
# race against an absent `instruments` table.
wait_for_api_health() {
    local max_attempts=30
    local attempt=0
    while [ "$attempt" -lt "$max_attempts" ]; do
        # jq parses JSON when available; grep is the fallback.
        if command -v jq >/dev/null 2>&1; then
            if curl -sf http://127.0.0.1:8000/health | jq -e '.status == "ok"' >/dev/null 2>&1; then
                echo "API healthy after $((attempt + 1)) attempt(s)"
                return 0
            fi
        else
            if curl -sf http://127.0.0.1:8000/health 2>/dev/null | grep -q '"status":"ok"'; then
                echo "API healthy after $((attempt + 1)) attempt(s)"
                return 0
            fi
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    echo "ERROR: API did not become healthy after $max_attempts attempts" >&2
    return 1
}

if ! wait_for_api_health; then
    echo "Refusing to start workers — API startup failed" >&2
    exit 1
fi

# ---- 2. supervisor-managed workers (first + derived subsets) ----
# ml-data-readiness PR-2 (2026-09-24): the daily chain is decomposed
# into two independently-runnable subsets. The "first" subset keeps the
# historic algotrader-moex-backfill slot (data-acquisition core). The
# new "derived" slot (algotrader-derived) runs corporate_actions,
# dividends, freshness_check, and guardian so they do NOT block behind
# a slow backfill_moex. Both slots share SQLite via WAL mode with 5s
# busy_timeout.
#
# db-bootstrap-hardening PR-3: by the time these slots are spawned,
# wait_for_api_health above has confirmed the api is healthy, which
# means run_migrations() has finished and the schema (including the
# `instruments` table) is settled. The historic "give worker 8s to
# release DB write-lock for migration phase" sleep is therefore
# unnecessary and was removed.
if ! pgrep -f 'worker.py daily' >/dev/null 2>&1; then
    nohup bash "$PROJECT/scripts/algotrader-supervisor.sh algotrader-moex-backfill" \
        "$PY" worker.py daily "$API_DIR" \
        >> "$LOG_DIR/algotrader-moex-backfill.log" 2>&1 &
    disown
    echo "[startup] worker launched (pid $!)"
else
    echo "[startup] worker already running"
fi

# ml-data-readiness PR-2: spawn the derived subset in parallel. It runs
# the corporate_actions / dividends / freshness_check / guardian phases
# which historically waited 39+ hours behind a stalled backfill_moex.
# Distinct supervisor slot (algotrader-derived) so its watchdog does not
# interfere with the algotrader-moex-backfill watchdog.
if ! pgrep -f 'worker.py daily derived' >/dev/null 2>&1; then
    nohup bash "$PROJECT/scripts/algotrader-supervisor.sh" \
        algotrader-derived \
        "$PY" worker.py daily derived "$API_DIR" \
        >> "$LOG_DIR/algotrader-derived.log" 2>&1 &
    disown
    echo "[startup] derived worker launched (pid $!)"
else
    echo "[startup] derived worker already running"
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
