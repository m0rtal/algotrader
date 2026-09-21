#!/usr/bin/env bash
# algotrader-supervisor — minimal restart loop.
# Usage:
#   ./algotrader-supervisor <service-name> <command...> [<cwd>]
#
# When a 4th argument is supplied, supervisor `cd`s there before
# launching the command on every iteration. This is the supported
# way to pin a service to a specific working directory (e.g.
# algotrader-api needs cwd=`apps/api` so `./data/state.db` resolves
# to the real DB and not a junk file in `$HOME`).
#
# When the 4th argument is absent, cwd stays whatever the caller
# started us with (legacy behaviour).
set -u
NAME="${1:?service name required}"
shift
SUP_CWD="${@: -1}"  # last positional argument if it looks like a directory
LOG="/home/hermes/.hermes/logs/${NAME}.log"
mkdir -p "$(dirname "$LOG")"
echo "[supervisor] $(date -Iseconds) starting $NAME: $*" >> "$LOG"

if [[ -d "$SUP_CWD" && "$SUP_CWD" != "." ]]; then
  # If the last argument is the cwd hint, strip it before exec.
  # Detect: any arg that is a directory path → treat as cwd hint.
  LAST_ARG="${@: -1}"
  if [[ -d "$LAST_ARG" ]]; then
    set -- "${@:1:$#-1}"  # drop last arg
  fi
  cd "$LAST_ARG" || echo "[supervisor] $(date -Iseconds) WARN: failed to cd $LAST_ARG" >> "$LOG"
  echo "[supervisor] $(date -Iseconds) cwd=$(pwd)" >> "$LOG"
fi

# Heartbeat watchdog (autonomous-chain-recovery phase 2). Look for a
# pipeline row with phase='worker.heartbeat' that is younger than 30
# minutes. If the latest is older (or absent), the worker is stalled —
# SIGKILL its PID so the outer restart loop relaunches it.
#
# Watchdog runs in the background; the main restart loop is the
# foreground. STATE_DB is `<cwd>/data/state.db` (matches the
# supervisor's cwd pinning convention used by worker.py).
STATE_DB="${PWD}/data/state.db"
HEARTBEAT_MAX_AGE_DAYS=0.0208  # 30 minutes in days (SQLite julianday unit)

_watchdog() {
  while true; do
    sleep 30
    if [[ ! -f "$STATE_DB" ]]; then
      continue
    fi
    # sqlite3 CLI may not be installed; use python via the
    # system python (the supervisor runs on the host, not in venv).
    AGE=$(STATE_DB="$STATE_DB" python3 -c "
import sqlite3, os, sys
p = os.environ.get('STATE_DB', '')
if not p or not os.path.exists(p):
    sys.exit(0)
con = sqlite3.connect(p, timeout=5)
row = con.execute(
    \"SELECT IFNULL(julianday('now') - julianday(MAX(finished_at)), 999) \"
    \"FROM pipeline WHERE phase='worker.heartbeat'\"
).fetchone()
print(row[0])
" 2>/dev/null)
    if [[ -n "$AGE" ]] && \
       awk -v a="$AGE" -v max="$HEARTBEAT_MAX_AGE_DAYS" \
         'BEGIN { exit !(a > max) }'; then
      echo "[supervisor] $(date -Iseconds) worker stalled \
(heartbeat age=${AGE}d, max=${HEARTBEAT_MAX_AGE_DAYS}d); killing" >> "$LOG"
      if [[ -n "${WORKER_PID:-}" ]]; then
        kill -9 "$WORKER_PID" 2>/dev/null || true
      fi
    fi
  done
}

_watchdog &
WATCHDOG_PID=$!

while true; do
  echo "[supervisor] $(date -Iseconds) launching $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1 &
  WORKER_PID=$!
  wait "$WORKER_PID"
  RC=$?
  WORKER_PID=""
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
