#!/usr/bin/env bash
# cron_liveness_check.sh — backstop for the algotrader worker(s).
#
# Why: the algotrader-supervisor.sh in-process watchdog only restarts
# on rc!=0 exit. When the worker dies silently (OOM kill, segfault,
# hang) without exit, nothing restarts it. This 2-minute cron reads
# the most recent worker heartbeat from the prod DB; if it's older
# than the stale threshold, the worker is stuck/dead and we SIGKILL it
# so the supervisor's restart loop takes over.
#
# 2026-10-01 fix: the daily worker (`worker.py daily first`) writes
# its heartbeat to the `pipeline` table (phase='worker.heartbeat'),
# NOT to `pipeline_heartbeat` (that table is written by the live-loop
# worker only). The original query read `pipeline_heartbeat` alone —
# when the live loop was not running, that row was days old, the cron
# declared the heartbeat stale, and SIGKILLed the *daily* worker every
# 2 minutes. The daily chain never survived past its first minutes and
# bars stopped accumulating (observed 2026-10-01: kill -9 at 08:00,
# 08:02, 08:04, 08:08, 08:10, 08:12 MSK).
#
# The query now takes the freshest heartbeat across BOTH stores, and
# the stale threshold is 10 minutes (600s) — the daily worker emits a
# `pipeline` heartbeat every ~5 minutes, so 300s was racing the
# emission interval.
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
STALE_THRESHOLD_SECONDS="${LIVENESS_STALE_THRESHOLD:-600}"

mkdir -p "$(dirname "$LOG")"
TS="$(date -Iseconds)"

# Read the freshest heartbeat across both stores and return its age in
# seconds. Returns empty string if neither table exists or both are
# empty.
HEARTBEAT_AGE_S="$(
DB="$DB" python3 <<'PYEOF' 2>/dev/null
import os, sqlite3, sys
from datetime import datetime, timezone
db = os.environ.get('DB', '')
if not db or not os.path.exists(db):
    sys.exit(0)

def _parse(iso):
    iso = iso.replace('T', ' ')
    for candidate in (iso, iso.split('.')[0].split('+')[0]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None

try:
    con = sqlite3.connect(db, timeout=5)
    candidates = []
    try:
        row = con.execute(
            "SELECT updated_at FROM pipeline_heartbeat "
            "ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            candidates.append(row[0])
    except sqlite3.OperationalError:
        pass
    try:
        row = con.execute(
            "SELECT MAX(finished_at) FROM pipeline "
            "WHERE phase='worker.heartbeat'"
        ).fetchone()
        if row and row[0]:
            candidates.append(row[0])
    except sqlite3.OperationalError:
        pass
    parsed = [p for p in (_parse(c) for c in candidates) if p is not None]
    if not parsed:
        sys.exit(0)
    ts = max(parsed)
    age_s = int((datetime.now(timezone.utc).replace(tzinfo=None) - ts).total_seconds())
    print(age_s)
except sqlite3.OperationalError:
    # Tables missing -> empty result so the bash fallback fires.
    sys.exit(0)
PYEOF
)"

# If neither heartbeat store has rows, fall back to checking worker
# process presence (weaker but real liveness signal).
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
