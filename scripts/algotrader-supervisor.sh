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
  # Tunables.
  HEARTBEAT_MAX_AGE_DAYS=0.0208  # 30 min
  PROGRESS_MAX_AGE_DAYS=0.0625   # 90 min — if bars count hasn't grown
                                   # in this window AND heartbeat is fresh,
                                   # main loop is stuck (HTTP/2 flow
                                   # control). Worker alive, daemon
                                   # heartbeat ticks, but real work
                                   # blocked.

  # Worker pid is written to PIDFILE by the main loop, since
  # subshell-exported vars don't propagate back. We poll the
  # pidfile every watchdog cycle.
  PIDFILE="${STATE_DB}.worker.pid"

  # Snapshot bars_count at worker start so we can detect "fresh
  # worker that's stuck from the very first Tinkoff call".
  BASELINE_BARS=$(STATE_DB="$STATE_DB" python3 << 'PYEOF'
import os, sqlite3
con = sqlite3.connect(os.environ['STATE_DB'], timeout=5)
print(con.execute("SELECT COUNT(*) FROM bars").fetchone()[0])
PYEOF
)
  BASELINE_BARS=${BASELINE_BARS##*$'\n'}  # last line only
  BASELINE_TIME=$(date +%s)

  while true; do
    sleep 30
    if [[ ! -f "$STATE_DB" ]]; then
      continue
    fi

    # Combined check via python (no sqlite3 CLI on this host).
    OUT=$(STATE_DB="$STATE_DB" HEARTBEAT_MAX="$HEARTBEAT_MAX_AGE_DAYS" \
    PROGRESS_MAX="$PROGRESS_MAX_AGE_DAYS" BASELINE_BARS="$BASELINE_BARS" \
    BASELINE_TIME="$BASELINE_TIME" python3 << 'PYEOF'
import os, sqlite3, time
from datetime import datetime
sd = os.environ['STATE_DB']
hb_max = float(os.environ['HEARTBEAT_MAX'])
pg_max = float(os.environ['PROGRESS_MAX'])
baseline_bars = int(os.environ.get('BASELINE_BARS', '0') or 0)
baseline_time = int(os.environ.get('BASELINE_TIME', '0') or 0)
con = sqlite3.connect(sd, timeout=5)
# Heartbeat freshness.
hb_row = con.execute(
    "SELECT finished_at FROM pipeline "
    "WHERE phase='worker.heartbeat' ORDER BY id DESC LIMIT 1"
).fetchone()
hb_age_days = 999.0
if hb_row:
    hb_age_days = (datetime.utcnow() - datetime.strptime(
        hb_row[0], "%Y-%m-%d %H:%M:%S"
    )).total_seconds() / 86400
# Bars progress.
last_bar = con.execute(
    "SELECT MAX(ts) FROM bars"
).fetchone()[0]
bar_age_days = 999.0
if last_bar:
    bar_age_days = (datetime.utcnow() - datetime.strptime(
        last_bar + " 00:00:00", "%Y-%m-%d %H:%M:%S"
    )).total_seconds() / 86400
# Baseline progress (compare current bars to snapshot at startup).
current_bars = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
bars_growth = current_bars - baseline_bars
elapsed = time.time() - baseline_time
print(f"hb_age={hb_age_days:.4f}d bar_age={bar_age_days:.4f}d bars_growth={bars_growth} elapsed={elapsed:.0f}s")
# Kill if any of:
# - heartbeat stale (worker truly dead)
# - worker alive >5 min but added 0 bars (stuck at startup)
#   (HTTP/2 flow control: fresh worker can be stuck from call 1).
# - heartbeat fresh AND no bars progress for >90 min
#   (worker alive but main loop stuck — HTTP/2 issue).
if hb_age_days > hb_max:
    print("STALL")
elif elapsed > 300 and bars_growth == 0:
    print("STUCK_AT_STARTUP")
elif hb_age_days < hb_max / 2 and bar_age_days > pg_max:
    print("STALL")
PYEOF
)

    # Always log diagnostic, kill on STALL/STUCK_AT_STARTUP.
    echo "[supervisor] $(date -Iseconds) watchdog check: $OUT" >> "$LOG"
    if grep -qE "STALL|STUCK_AT_STARTUP" <<< "$OUT"; then
      WORKER_PID=""
      if [[ -f "$PIDFILE" ]]; then
        WORKER_PID=$(cat "$PIDFILE" 2>/dev/null)
      fi
      if [[ -n "$WORKER_PID" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
        echo "[supervisor] $(date -Iseconds) killing stalled worker pid=$WORKER_PID" >> "$LOG"
        kill -9 "$WORKER_PID" 2>/dev/null || true
      fi
    fi
  done
}

_watchdog &
WATCHDOG_PID=$!

# Pidfile location shared with the watchdog subshell.
PIDFILE="${STATE_DB}.worker.pid"

while true; do
  echo "[supervisor] $(date -Iseconds) launching $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1 &
  WORKER_PID=$!
  echo "$WORKER_PID" > "$PIDFILE"
  wait "$WORKER_PID"
  RC=$?
  WORKER_PID=""
  rm -f "$PIDFILE"
  echo "[supervisor] $(date -Iseconds) $NAME exited rc=$RC; restarting in 5s" >> "$LOG"
  sleep 5
done
