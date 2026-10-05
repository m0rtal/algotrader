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
if [[ ! "$NAME" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]; then
  printf '%s\n' '[supervisor] unknown:service-name' >&2
  exit 2
fi
HELPER="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/worker-watchdog.py"
SUPERVISOR_PID=$$
shift
# The command to run is everything between NAME and the optional trailing
# directory hint. IMPORTANT: pass the command as separate args (one per
# word). Quoting the whole command as a single string causes "$@" to
# treat it as one executable path, which fails with "no such file or
# directory" (the spaces in the path get in the way).
#   ✗   bash supervisor.sh foo "python3 worker.py daily" /path/to/cwd
#   ✓   bash supervisor.sh foo python3 worker.py daily /path/to/cwd
SUP_CWD="${@: -1}"  # last positional argument if it looks like a directory
LOG="${ALGO_SUPERVISOR_LOG:-/home/hermes/.hermes/logs/${NAME}.log}"
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

# Each role checks only its verified current child through the pidfd helper.
# Resolve state from the existing cwd convention before deriving the target.
STATE_DB="${PWD}/data/state.db"
PIDFILE="${STATE_DB}.${NAME}.worker.pid"
HEARTBEAT_MAX_AGE_DAYS=0.0208

_watchdog() {
  while true; do
    sleep 30
    OUT=$(env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 "$HELPER" \
      --db "$STATE_DB" --pid-file "$PIDFILE" --parent "$SUPERVISOR_PID" \
      --max-age-days "$HEARTBEAT_MAX_AGE_DAYS" 2>/dev/null) || OUT='unknown:helper'
    case "$OUT" in
      fresh|grace|stale:signaled|unknown:*) ;;
      *) OUT='unknown:helper-output' ;;
    esac
    # Bound unexpected output; the helper never emits raw exception messages.
    if (( ${#OUT} > 79 )); then OUT='unknown:helper-output'; fi
    echo "[supervisor] $(date -Iseconds) watchdog check: $OUT" >> "$LOG"
  done
}
_watchdog &
WATCHDOG_PID=$!

while true; do
  echo "[supervisor] $(date -Iseconds) launching $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1 &
  WORKER_PID=$!
  flock -w 5 "${PIDFILE}.lock" bash -c \
    'printf "%s\n" "$2" > "$1"' _ "$PIDFILE" "$WORKER_PID" \
    || echo '[supervisor] unknown:pid-publish' >> "$LOG"
  wait "$WORKER_PID"
  RC=$?
  flock -w 5 "${PIDFILE}.lock" bash -c \
    'if [[ -f "$1" && "$(<"$1")" == "$2" ]]; then rm -f -- "$1"; fi' \
    _ "$PIDFILE" "$WORKER_PID" \
    || echo '[supervisor] unknown:pid-cleanup' >> "$LOG"
  WORKER_PID=""
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
