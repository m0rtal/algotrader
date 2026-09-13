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

while true; do
  echo "[supervisor] $(date -Iseconds) launching $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1
  RC=$?
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
