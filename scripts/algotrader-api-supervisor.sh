#!/usr/bin/env bash
# algotrader-api-supervisor — supervisor for the FastAPI/uvicorn backend.
#
# Why a separate script from algotrader-supervisor.sh?
# The worker supervisor's watchdog polls SQLite for heartbeat + bars
# progress, because the worker's "alive" signal is a DB row. The
# backend has no DB heartbeat — its "alive" signal is the /health
# HTTP endpoint on port 8000. So this supervisor gets its own
# watchdog that hits /health every 10s; if it stops responding, the
# uvicorn process is killed and the outer restart loop relaunches.
#
# Usage:
#   ./algotrader-api-supervisor [--bind HOST:PORT]
#
# Environment:
#   ALGOTRADER_API_BIND  override bind address (default 0.0.0.0:8000)
#
# Idempotent: safe to invoke from cron, @reboot, or manually. The
# restart loop is in-process, so a single invocation manages the
# backend until the host shuts down.
set -u
NAME="algotrader-api"
BIND="${ALGOTRADER_API_BIND:-0.0.0.0:8000}"
HOST="${BIND%:*}"
PORT="${BIND##*:}"
LOG="/home/hermes/.hermes/logs/${NAME}.log"
PIDFILE="/home/hermes/algotrader/apps/api/data/${NAME}.pid"
APPS_API="/home/hermes/algotrader/apps/api"
UVICORN="${APPS_API}/.venv/bin/uvicorn"
HEALTH_URL="http://127.0.0.1:${PORT}/health"

# PR #125 (2026-09-24): disable OpenTelemetry SDK by default.
# The app configures ``OTLPSpanExporter(endpoint=http://localhost:4317)``
# at module level. When no OTel collector is listening on that port, the
# BatchSpanProcessor's background export thread gets stuck in an
# exponential-backoff retry loop against a refused TCP connection. The
# thread join in ``processor.shutdown()`` then blocks uvicorn's lifespan
# exit, so the api-supervisor watchdog sees /health fail (or hangs on
# shutdown), kills uvicorn with SIGKILL, and enters a tight restart loop
# that never converges. Set ``OTEL_SDK_DISABLED=true`` so the exporter
# is a no-op. Operators who *do* run a collector can override this by
# exporting ``ALGOTRADER_API_OTEL=1`` before launching the supervisor.
if [ -z "${ALGOTRADER_API_OTEL:-}" ]; then
  export OTEL_SDK_DISABLED=true
fi
HEALTH_TIMEOUT_S=3
HEALTH_MAX_AGE_S=30   # 3 failed health checks → kill

mkdir -p "$(dirname "$LOG")"
echo "[api-supervisor] $(date -Iseconds) starting $NAME on $BIND (log=$LOG pidfile=$PIDFILE)" >> "$LOG"

cd "$APPS_API" || { echo "[api-supervisor] $(date -Iseconds) FATAL: cd $APPS_API failed" >> "$LOG"; exit 1; }

_watchdog() {
  # Poll /health. If HEALTH_MAX_FAILS consecutive checks fail, kill the
  # uvicorn process so the outer restart loop relaunches it.
  local consecutive_failures=0
  local max_fails=$((HEALTH_MAX_AGE_S / 10))

  while true; do
    sleep 10
    if curl -m "$HEALTH_TIMEOUT_S" -sf "$HEALTH_URL" >/dev/null 2>&1; then
      consecutive_failures=0
      continue
    fi
    consecutive_failures=$((consecutive_failures + 1))
    echo "[api-supervisor] $(date -Iseconds) health check failed ($consecutive_failures/$max_fails)" >> "$LOG"
    if (( consecutive_failures >= max_fails )); then
      WORKER_PID=""
      if [[ -f "$PIDFILE" ]]; then
        WORKER_PID=$(cat "$PIDFILE" 2>/dev/null)
      fi
      if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "[api-supervisor] $(date -Iseconds) killing stalled uvicorn pid=$WORKER_PID" >> "$LOG"
        kill -9 "$WORKER_PID" 2>/dev/null || true
      else
        # Pidfile gone but health still failing — pkill any uvicorn
        # bound to our port and let the outer loop's wait return.
        echo "[api-supervisor] $(date -Iseconds) pidfile $PIDFILE missing; pkilling uvicorn on port $PORT" >> "$LOG"
        pkill -9 -f "uvicorn algotrader_api.main.*--port $PORT" 2>/dev/null || true
      fi
      consecutive_failures=0
    fi
  done
}

_watchdog &
WATCHDOG_PID=$!

while true; do
  echo "[api-supervisor] $(date -Iseconds) launching uvicorn on $BIND" >> "$LOG"
  "$UVICORN" algotrader_api.main:app --host "$HOST" --port "$PORT" >> "$LOG" 2>&1 &
  WORKER_PID=$!
  echo "$WORKER_PID" > "$PIDFILE"
  wait "$WORKER_PID"
  RC=$?
  WORKER_PID=""
  rm -f "$PIDFILE"
  echo "[api-supervisor] $(date -Iseconds) uvicorn exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
