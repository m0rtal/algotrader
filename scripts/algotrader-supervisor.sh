#!/usr/bin/env bash
# algotrader-supervisor — minimal restart loop.
# Usage: ./algotrader-supervisor <service-name> <command...>
set -u
NAME="${1:?service name required}"
shift
LOG="/home/hermes/.hermes/logs/${NAME}.log"
mkdir -p "$(dirname "$LOG")"
echo "[supervisor] $(date -Iseconds) starting $NAME: $*" >> "$LOG"
while true; do
  echo "[supervisor] $(date -Iseconds) launching $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  RC=$?
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
