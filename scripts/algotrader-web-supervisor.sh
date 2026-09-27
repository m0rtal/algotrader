#!/usr/bin/env bash
# algotrader-web-supervisor — minimal restart loop for vite dev server.
# Lighter than algotrader-supervisor.sh: no worker-state watchdog
# (vite doesn't write to state.db; the watchdog would crash on the
# missing file). Just keep the process alive; if it dies, restart.
#
# Usage:
#   ./algotrader-web-supervisor
set -u
NAME="algotrader-web"
WEB_DIR="/home/hermes/algotrader/apps/web"
LOG="/home/hermes/.hermes/logs/${NAME}.log"
mkdir -p "$(dirname "$LOG")"
echo "[supervisor] $(date -Iseconds) starting $NAME in $WEB_DIR" >> "$LOG"

while true; do
  cd "$WEB_DIR" || { echo "[supervisor] $(date -Iseconds) FATAL: cd $WEB_DIR failed" >> "$LOG"; sleep 30; continue; }
  echo "[supervisor] $(date -Iseconds) launching npm run dev -- --host 0.0.0.0" >> "$LOG"
  npm run dev -- --host 0.0.0.0 >> "$LOG" 2>&1
  RC=$?
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
