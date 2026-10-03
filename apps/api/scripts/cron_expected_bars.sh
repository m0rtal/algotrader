#!/usr/bin/env bash
# Hourly data-only refresh; add after PR merge, without enabling code crons:
# 17 * * * * /home/hermes/algotrader/apps/api/scripts/cron_expected_bars.sh
#
# The one-shot Python writer commits atomically. This wrapper:
#  * keeps its own ${DB}.expected-bars.lock as wrapper-local
#    duplicate-invocation protection (not the spec lock),
#  * maps child rc=75 (shared writer-lock busy) to
#    `DEFER writer-lock-busy` + wrapper exit 0 (no SQLite-busy
#    retry),
#  * retries SQLite "database is locked" three times for any
#    non-75 child failure whose output contains that string,
#  * preserves the existing failure-to-run-path behaviour.
set -eu
umask 077

API_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PY="${ALGOTRADER_EXPECTED_BARS_PYTHON:-${API_DIR}/.venv/bin/python}"
SCRIPT="${API_DIR}/scripts/populate_expected_bars.py"
DB="${API_DIR}/data/state.db"
LOG="/home/hermes/.hermes/logs/algotrader-expected-bars.log"

while (( $# )); do
    case "$1" in
        --db|--log)
            if (( $# < 2 )); then printf 'missing value for %s\n' "$1" >&2; exit 2; fi
            if [[ "$1" == --db ]]; then DB="$2"; else LOG="$2"; fi
            shift 2 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

mkdir -p "$(dirname "$LOG")"
_log() { printf '[%s] %s\n' "$(date -Is)" "$*" >> "$LOG"; }
if [[ ! -f "$DB" || ! -x "$PY" || ! -f "$SCRIPT" ]]; then
    _log "ERROR expected-bars prerequisites missing"
    exit 2
fi

# A lock next to the DB prevents two scheduled invocations of this
# wrapper from racing. The file remains in place; flock releases
# automatically when fd 9 closes. This is the WRAPPER-LOCAL
# duplicate-invocation protection; the canonical spec lock is
# ${DB}.writer.lock and is acquired by the Python writer itself.
exec 9>>"${DB}.expected-bars.lock"
if ! flock -n 9; then
    _log "ERROR locked: another expected-bars run is active"
    exit 75
fi

for attempt in 1 2 3; do
    # Capture the child rc explicitly BEFORE inspecting the output
    # so a child-rc=75 outcome is never misread as a SQLite
    # "database is locked" retry candidate.
    child_rc=0
    output=$("$PY" "$SCRIPT" --db "$DB" 2>&1) || child_rc=$?
    if (( child_rc == 0 )); then
        if [[ "$output" =~ OK:[[:space:]]populated[[:space:]]expected_bars[[:space:]]for[[:space:]]([0-9]+)[[:space:]]figis ]]; then
            _log "SUCCESS expected-bars processed=${BASH_REMATCH[1]} attempt=$attempt"
            exit 0
        fi
        _log "ERROR unexpected writer output despite rc=0"
        exit 1
    fi
    if (( child_rc == 75 )); then
        # Shared writer lock is busy: log a bounded deferral line
        # and let the next scheduled invocation retry. No
        # SQLite-busy retry loop here.
        _log "DEFER writer-lock-busy attempt=$attempt"
        exit 0
    fi
    if [[ "$output" == *"database is locked"* && "$attempt" -lt 3 ]]; then
        _log "RETRY SQLite database is locked attempt=$attempt"
        sleep 2
        continue
    fi
    _log "ERROR expected-bars refresh failed attempt=$attempt rc=$child_rc"
    exit 1
done
