#!/usr/bin/env bash
# cron_liveness_check.sh — backstop for the algotrader-live worker.
#
# Why: the algotrader-supervisor.sh in-process watchdog only restarts
# on rc≠0 exit. When the worker dies silently (OOM kill, segfault,
# hang) without exit, nothing restarts it. This 2-minute cron reads
# the most recent pipeline_heartbeat.updated_at from the prod DB; if
# it's older than 5 minutes (300s), the worker is stuck/dead and we
# SIGKILL it so the supervisor's restart loop takes over.
#
# Catches the failure modes the in-process watchdog misses:
#   - Supervisor itself dies (so its watchdog dies with it)
#   - Worker process alive but stuck (e.g., infinite loop in a
#     Tinkoff retry) — supervisor won't restart because rc=0
#   - Worker died silently without an exit (the actual failure mode
#     we observed 2026-09-27 ~13:44 MSK).
#
# Pattern reference: scripts/cron_api_healthcheck.sh (PR #121).
# SQL access: python3 heredoc (mirrors algotrader-supervisor.sh
# watchdog — sqlite3 CLI is not installed on this host).

set -u

WORKER_NAME="${ALGOTRADER_LIVE_WORKER:-algotrader-moex-backfill}"
DB="${ALGOTRADER_STATE_DB:-/home/hermes/algotrader/apps/api/data/state.db}"
LOG="/home/hermes/.hermes/logs/algotrader-liveness-cron.log"
STALE_THRESHOLD_SECONDS="${LIVENESS_STALE_THRESHOLD:-300}"

mkdir -p "$(dirname "$LOG")"
TS="$(date -Iseconds)"

# Read most recent heartbeat.updated_at and return its age in seconds.
# Returns empty string if the table doesn't exist (pre-Task-2 schema)
# or has no rows.
HEARTBEAT_AGE_S="$(
DB="$DB" python3 <<'PYEOF' 2>/dev/null
import os, sqlite3, sys
from datetime import datetime, timezone
db = os.environ.get('DB', '')
if not db or not os.path.exists(db):
    sys.exit(0)
try:
    con = sqlite3.connect(db, timeout=5)
    row = con.execute(
        "SELECT updated_at FROM pipeline_heartbeat "
        "ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if not row:
        sys.exit(0)
    iso = row[0]
    # Tolerate both "YYYY-MM-DDTHH:MM:SS" (worker.py writes) and
    # "YYYY-MM-DD HH:MM:SS" (julianday() default). The T→space swap is
    # intentional and only fires if the timestamp lacks a 'T' or ' '.
    iso_clean = iso.replace('T', ' ')
    if 'T' not in iso and ' ' not in iso:
        iso_clean = iso
    try:
        ts = datetime.fromisoformat(iso_clean)
    except ValueError:
        # Last-resort: strip microseconds + timezone suffix.
        iso_clean = iso.split('.')[0].split('+')[0].replace('T', ' ')
        ts = datetime.fromisoformat(iso_clean)
    age_s = int((datetime.now(timezone.utc).replace(tzinfo=None) - ts).total_seconds())
    print(age_s)
except sqlite3.OperationalError:
    # Table missing → empty result so the bash fallback fires.
    sys.exit(0)
PYEOF
)"

# If the heartbeat table doesn't exist (live mode never started) OR no
# rows, fall back to checking worker process presence. Two reasons:
#   1. Pre-Task-2 deployments don't have the table yet; we still want
#      the cron to be useful (process-presence is a weaker but real
#      liveness signal).
#   2. A fresh boot where the worker hasn't yet written its first
#      heartbeat — pgrep is the next-best check.
if [ -z "$HEARTBEAT_AGE_S" ]; then
    if pgrep -f 'worker.py daily first' >/dev/null 2>&1; then
        echo "[$TS] no heartbeat row but worker process alive; ok." >> "$LOG"
        exit 0
    fi
    echo "[$TS] FATAL: no heartbeat row and no worker process. Relaunching via startup-algotrader.sh." >> "$LOG"
    bash /home/hermes/algotrader/scripts/startup-algotrader.sh >> /home/hermes/.hermes/logs/algotrader-startup.log 2>&1
    echo "[$TS] startup-algotrader.sh invoked rc=$?" >> "$LOG"
    exit 0
fi

if [ "$HEARTBEAT_AGE_S" -lt "$STALE_THRESHOLD_SECONDS" ]; then
    # Healthy.
    echo "[$TS] heartbeat fresh: ${HEARTBEAT_AGE_S}s < ${STALE_THRESHOLD_SECONDS}s; ok." >> "$LOG"
    exit 0
fi

# Stale. Kill the worker so the supervisor restarts it.
WORKER_PID="$(pgrep -f 'worker.py daily first' | head -1 || echo "")"
echo "[$TS] FATAL: heartbeat stale ${HEARTBEAT_AGE_S}s > ${STALE_THRESHOLD_SECONDS}s. Killing worker (pid=${WORKER_PID:-none})." >> "$LOG"

if [ -n "$WORKER_PID" ]; then
    kill -9 "$WORKER_PID" 2>&1 >> "$LOG"
    echo "[$TS] kill -9 sent to pid=$WORKER_PID rc=$?" >> "$LOG"
else
    echo "[$TS] WARN: no worker pid found via pgrep, but heartbeat is stale. Worker likely already dead; supervisor will relaunch on its own schedule." >> "$LOG"
fi

exit 0
