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
# 2026-10-01 fix (process-isolation): the script accepts env-overridable
# boundaries for every external touch point (log, lock, pgrep, kill,
# setsid, supervisor launcher, argv-match tables). This lets the test
# suite stub all process control in tmp_path and proves the watchdog
# cannot affect production from a test run. See
# apps/api/tests/test_cron_liveness_check.py for the harness.
#
# 2026-10-01 fix (missing first worker): the previous logic returned
# early on any fresh heartbeat, even when BOTH the first worker and the
# supervisor were absent. The derived worker (live-loop) emits its own
# pipeline heartbeat, so the first worker could remain dead
# indefinitely. Now we re-check process presence after a fresh
# heartbeat and dispatch the clean supervisor wrapper if both are
# gone. Aggregate heartbeat is not per-worker stall proof — we make
# that boundary explicit.
#
# 2026-10-01 fix (argv selection): pgrep -f matches substrings of any
# 16kB-long command line, so diagnostic shells, unrelated commands,
# and subset commands (`worker.py live`, `worker.py daily second`)
# could be picked up. Process selection now requires argv tokens to
# appear IN ORDER in /proc/$PID/cmdline, matching exactly the
# configured LIVENESS_FIRST_MATCH_FILE / LIVENESS_SUPERVISOR_MATCH_FILE
# pattern. Broad pgrep is replaced by a narrowed scan that verifies
# each candidate's argv before any kill or recovery dispatch.
#
# 2026-10-02 fix (role disambiguation): the supervisor and the
# daily-first worker share the include-tokens 'worker.py daily first'
# in argv, so the include-only match selected the supervisor PID as
# the first worker (cron log: 'first worker present (pid=1785)' but
# pid=1785 was bash algotrader-supervisor.sh; the actual daily-first
# worker was pid=1795). Selection now also applies a deny-table
# (LIVENESS_FIRST_DENY_FILE, default tokens 'algotrader-supervisor.sh'
# and 'algotrader-moex-backfill') to the first-worker scan: a
# candidate carrying any deny token is the supervisor (or its launcher
# wrapper), not the worker, and is excluded from FIRST_PIDS. The
# supervisor scan keeps include-only matching so the recovery branch
# can still detect a live supervisor.
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
LOG="${LIVENESS_LOG:-/home/hermes/.hermes/logs/algotrader-liveness-cron.log}"
LOCK_FILE="${LIVENESS_LOCK_FILE:-$(dirname "$DB")/cron_liveness.lock}"
# Single-flight lock directory. mkdir is atomic on POSIX; only one
# cron tick holds the dir at a time. The directory lives next to the
# lock file and is removed on EXIT (trap below).
LOCK_DIR="${LIVENESS_LOCK_DIR:-${LOCK_FILE}.d}"
STALE_THRESHOLD_SECONDS="${LIVENESS_STALE_THRESHOLD:-600}"

# External-command boundaries (overridable so tests cannot touch prod).
PGREP_BIN="${LIVENESS_PGREP_BIN:-/usr/bin/pgrep}"
KILL_BIN="${LIVENESS_KILL_BIN:-/bin/kill}"
SETSID_BIN="${LIVENESS_SETSID_BIN:-/usr/bin/setsid}"
SUPERVISOR_LAUNCH="${LIVENESS_SUPERVISOR_LAUNCH:-/home/hermes/algotrader/scripts/algotrader-moex-supervisor-clean.sh}"
# When set (test mode only), dispatch synchronously so the test harness
# can observe the launcher invocation before the script returns.
# Production NEVER sets this; the supervisor is detached via setsid.
FOREGROUND_DISPATCH="${LIVENESS_FOREGROUND_DISPATCH:-0}"

# Argv match tables. NUL-separated tokens (no NULs in argv on Linux).
# When LIVENESS_*_MATCH_FILE is unset (production), a baked-in default
# is used. This guarantees process selection requires argv identity
# even when no override is provided.
FIRST_MATCH_FILE="${LIVENESS_FIRST_MATCH_FILE:-}"
SUPERVISOR_MATCH_FILE="${LIVENESS_SUPERVISOR_MATCH_FILE:-}"
# Optional deny-table for the first-worker role. NUL-separated argv
# tokens that MUST NOT appear in the candidate's cmdline. Used to
# distinguish the worker subprocess from the supervisor that launched
# it: their include-tokens ('worker.py daily first') overlap, so the
# include-table alone cannot tell them apart. The deny-table carries
# the supervisor-specific tokens ('algotrader-supervisor.sh',
# 'algotrader-moex-backfill'); a candidate carrying them is the
# supervisor, not the worker, and is excluded from FIRST_PIDS.
FIRST_DENY_FILE="${LIVENESS_FIRST_DENY_FILE:-}"

# Default match tables used when the override env var is empty. Each
# is a NUL-separated list of argv tokens that must appear, in order,
# in /proc/$PID/cmdline for the PID to be selected as that role.
# Overrides via LIVENESS_FIRST_MATCH_FILE / LIVENESS_SUPERVISOR_MATCH_FILE
# are honoured by the test harness.
if [ -z "$FIRST_MATCH_FILE" ]; then
    FIRST_MATCH_FILE="$(mktemp)"
    printf 'worker.py\0daily\0first\0' > "$FIRST_MATCH_FILE"
    FIRST_MATCH_FILE_CLEANUP=1
fi
if [ -z "$SUPERVISOR_MATCH_FILE" ]; then
    SUPERVISOR_MATCH_FILE="$(mktemp)"
    printf 'algotrader-supervisor.sh\0algotrader-moex-backfill\0worker.py\0daily\0first\0' > "$SUPERVISOR_MATCH_FILE"
    SUPERVISOR_MATCH_FILE_CLEANUP=1
fi
if [ -z "$FIRST_DENY_FILE" ]; then
    # Supervisor-only tokens. Any candidate carrying these is the
    # supervisor (or its launcher wrapper), not the daily-first worker.
    FIRST_DENY_FILE="$(mktemp)"
    printf 'algotrader-supervisor.sh\0algotrader-moex-backfill\0' > "$FIRST_DENY_FILE"
    FIRST_DENY_FILE_CLEANUP=1
fi

# Single EXIT trap: cleanup temp match files (production defaults).
_cleanup_match_files() {
    if [ "${FIRST_MATCH_FILE_CLEANUP:-0}" = "1" ] && [ -f "${FIRST_MATCH_FILE:-}" ]; then
        rm -f "$FIRST_MATCH_FILE" || true
    fi
    if [ "${SUPERVISOR_MATCH_FILE_CLEANUP:-0}" = "1" ] && [ -f "${SUPERVISOR_MATCH_FILE:-}" ]; then
        rm -f "$SUPERVISOR_MATCH_FILE" || true
    fi
    if [ "${FIRST_DENY_FILE_CLEANUP:-0}" = "1" ] && [ -f "${FIRST_DENY_FILE:-}" ]; then
        rm -f "$FIRST_DENY_FILE" || true
    fi
}
trap _cleanup_match_files EXIT

mkdir -p "$(dirname "$LOG")"
mkdir -p "$(dirname "$LOCK_FILE")"
mkdir -p "$(dirname "$LOCK_DIR")"
TS="$(date -Iseconds)"

# Single-flight lock: mkdir is atomic on POSIX. Only one watchdog run
# may dispatch a supervisor at a time. The directory is removed on EXIT.
# (flock on independently-opened FDs is not cross-process safe on Linux;
# mkdir avoids the open-OFD pitfall.)
acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        return 0
    fi
    return 1
}

release_lock() {
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

# Read a NUL-separated argv-match table from a file. Tokens must
# appear in order in the candidate's argv. Returns 0 if match, 1
# otherwise. An optional third argument is a NUL-separated deny
# table: any deny token present in the candidate's cmdline rejects
# the match (used to exclude the supervisor argv from the worker
# role — both processes carry 'worker.py daily first', but only the
# supervisor carries 'algotrader-supervisor.sh'). If match-file is
# unset or unreadable, return 1 (fail-closed): an empty match table
# is a misconfiguration and must never select a candidate.
argv_matches_pid() {
    local pid="$1"
    local match_file="$2"
    local deny_file="${3:-}"
    local cmdline tokens i arg

    if [ -z "$match_file" ] || [ ! -r "$match_file" ]; then
        # Fail-closed: a missing/unreadable match table is a
        # misconfiguration. The earlier 'legacy accept' branch
        # silently accepted every /proc-readable candidate when
        # the env override was empty, which made process selection
        # dependent on the default-table creation above succeeding.
        return 1
    fi

    [ -r "/proc/$pid/cmdline" ] || return 1
    # Read NUL-separated cmdline; replace NULs with newlines for shell parsing.
    local cmdline_str
    cmdline_str="$(tr '\0' '\n' < "/proc/$pid/cmdline")"

    # Deny-table first: reject the candidate outright if it carries any
    # supervisor-only token. Cheap O(N×M) scan over a tiny argv (the
    # worker is single-threaded, the deny table is <10 entries).
    if [ -n "$deny_file" ] && [ -r "$deny_file" ]; then
        local darg dline
        while IFS= read -r -d '' darg; do
            [ -n "$darg" ] || continue
            while IFS= read -r dline; do
                if [ "$dline" = "$darg" ]; then
                    return 1
                fi
            done <<EOF
$cmdline_str
EOF
        done < "$deny_file"
    fi

    i=0
    # Walk tokens in match-file in order; each must appear later in cmdline_str.
    while IFS= read -r -d '' arg; do
        [ -n "$arg" ] || continue
        # Find the token at position >= i in cmdline_str.
        local remaining="${cmdline_str}"
        local found=0
        local line
        local idx=0
        while IFS= read -r line; do
            if [ "$idx" -ge "$i" ]; then
                if [ "$line" = "$arg" ]; then
                    i=$((idx + 1))
                    found=1
                    break
                fi
            fi
            idx=$((idx + 1))
        done <<EOF
$remaining
EOF
        if [ "$found" -ne 1 ]; then
            return 1
        fi
    done < "$match_file"

    return 0
}

# List PIDs whose argv matches the given match-file. Uses PGREP_BIN
# (overrideable) for the candidate scan, then filters by argv. An
# optional second argument is a NUL-separated deny-table whose tokens
# MUST NOT appear in the candidate's argv (role-disambiguation).
find_matching_pids() {
    local match_file="$1"
    local deny_file="${2:-}"
    local candidate pid matched=() rc=1

    # Broad scan: any process with the worker-name substring is a
    # candidate; we filter by argv below. Use of -f mirrors the
    # baseline pgrep pattern but the result is narrowed before any
    # destructive action.
    while IFS= read -r candidate; do
        [ -n "$candidate" ] || continue
        [ "$candidate" = "$$" ] && continue  # never select self
        if argv_matches_pid "$candidate" "$match_file" "$deny_file"; then
            matched+=("$candidate")
            rc=0
        fi
    done < <("$PGREP_BIN" -f "$WORKER_NAME" 2>/dev/null || true)

    if [ "${#matched[@]}" -gt 0 ]; then
        printf '%s\n' "${matched[@]}"
    fi
    return "$rc"
}

# Dispatch the clean supervisor wrapper. Production: detached via
# setsid so the supervisor outlives the cron tick. The lock directory
# is released before the daemon forks so the daemonised supervisor
# does not retain the watchdog lock forever. Tests (LIVENESS_FOREGROUND_DISPATCH=1):
# synchronous so the harness can observe the launcher invocation
# before the script returns; lock is held for the duration of the
# launcher run, then released by the caller.
dispatch_clean_supervisor() {
    local ts="$1"
    if [ "$FOREGROUND_DISPATCH" = "1" ]; then
        "$SETSID_BIN" bash "$SUPERVISOR_LAUNCH" >> "$LOG" 2>&1 < /dev/null
        echo "[$ts] clean supervisor dispatched (foreground) via $SUPERVISOR_LAUNCH rc=$?" >> "$LOG"
    else
        # Release the lock BEFORE backgrounding so the daemon does not
        # inherit the lock directory. Subsequent cron ticks see no
        # lock and may proceed; if the previous dispatch is still
        # initialising, the next tick will see a fresh heartbeat and
        # the supervisor already-pending — so no duplicate dispatch.
        release_lock
        "$SETSID_BIN" bash "$SUPERVISOR_LAUNCH" >> "$LOG" 2>&1 < /dev/null &
        echo "[$ts] clean supervisor dispatched pid=$! via $SUPERVISOR_LAUNCH" >> "$LOG"
    fi
}

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

# Single-flight: only one watchdog run may dispatch a supervisor at a time.
if ! acquire_lock; then
    echo "[$TS] another liveness run is in progress; exiting." >> "$LOG"
    exit 0
fi

# Resolve process presence once and reuse below. We pass the resolved
# first-worker / supervisor PID lists into the recovery branch so we
# never re-scan with broad pgrep. The first-worker scan uses the
# deny-table to exclude the supervisor argv — both processes carry
# the same include-tokens 'worker.py daily first', so without the deny
# filter the supervisor PID would be killed instead of the worker
# (observed 2026-10-02: cron log named pid=1785 as the first worker
# while the actual daily-first worker was pid=1795).
read -r -a FIRST_PIDS < <(find_matching_pids "$FIRST_MATCH_FILE" "$FIRST_DENY_FILE" | tr '\n' ' ')
read -r -a SUPERVISOR_PIDS < <(find_matching_pids "$SUPERVISOR_MATCH_FILE" | tr '\n' ' ')

# If neither heartbeat store has rows, fall back to checking worker
# process presence (weaker but real liveness signal).
if [ -z "$HEARTBEAT_AGE_S" ]; then
    if [ "${#FIRST_PIDS[@]}" -gt 0 ] && [ -n "${FIRST_PIDS[0]:-}" ]; then
        echo "[$TS] no heartbeat row but worker process alive; ok." >> "$LOG"
        release_lock
        exit 0
    fi
    echo "[$TS] FATAL: no heartbeat row and no worker process. Dispatching clean supervisor wrapper." >> "$LOG"
    dispatch_clean_supervisor "$TS"
    release_lock
    exit 0
fi

# Heartbeat exists. Even if fresh, the aggregate heartbeat is not
# per-worker proof. Verify the daily-first worker is still present;
# if BOTH the first worker AND the supervisor are gone, dispatch
# recovery immediately. (A derived worker's heartbeat must not hide
# a missing first worker.)
if [ "$HEARTBEAT_AGE_S" -lt "$STALE_THRESHOLD_SECONDS" ]; then
    if [ "${#FIRST_PIDS[@]}" -gt 0 ] && [ -n "${FIRST_PIDS[0]:-}" ]; then
        echo "[$TS] heartbeat fresh: ${HEARTBEAT_AGE_S}s < ${STALE_THRESHOLD_SECONDS}s; first worker present (pid=${FIRST_PIDS[0]}); ok." >> "$LOG"
        release_lock
        exit 0
    fi
    # Fresh heartbeat but no first worker. If the supervisor is alive
    # it will respawn the first worker on its own restart loop; do
    # not duplicate the supervisor.
    if [ "${#SUPERVISOR_PIDS[@]}" -gt 0 ] && [ -n "${SUPERVISOR_PIDS[0]:-}" ]; then
        echo "[$TS] heartbeat fresh but no first worker; supervisor alive (pid=${SUPERVISOR_PIDS[0]}); its restart loop will respawn. ok." >> "$LOG"
        release_lock
        exit 0
    fi
    # Neither present: dispatch the clean supervisor wrapper.
    echo "[$TS] heartbeat fresh (${HEARTBEAT_AGE_S}s) but missing first worker and supervisor; dispatching clean supervisor via $SUPERVISOR_LAUNCH" >> "$LOG"
    dispatch_clean_supervisor "$TS"
    release_lock
    exit 0
fi

# Stale. Kill the matched first worker so the supervisor restarts it.
WORKER_PID=""
if [ "${#FIRST_PIDS[@]}" -gt 0 ] && [ -n "${FIRST_PIDS[0]:-}" ]; then
    WORKER_PID="${FIRST_PIDS[0]}"
fi
echo "[$TS] FATAL: heartbeat stale ${HEARTBEAT_AGE_S}s > ${STALE_THRESHOLD_SECONDS}s. Killing first worker (pid=${WORKER_PID:-none})." >> "$LOG"

if [ -n "$WORKER_PID" ]; then
    "$KILL_BIN" -9 "$WORKER_PID" >> "$LOG" 2>&1
    echo "[$TS] kill -9 sent to pid=$WORKER_PID rc=$?" >> "$LOG"
else
    # No matching first worker process. If the supervisor is alive it
    # will respawn the worker on its own restart loop. If NEITHER is
    # alive (supervisor died silently too), nothing else would ever
    # relaunch the chain — dispatch the clean supervisor wrapper.
    if [ "${#SUPERVISOR_PIDS[@]}" -gt 0 ] && [ -n "${SUPERVISOR_PIDS[0]:-}" ]; then
        echo "[$TS] supervisor alive without first worker; its restart loop will respawn." >> "$LOG"
    else
        echo "[$TS] no first worker AND no supervisor; dispatching clean supervisor wrapper via $SUPERVISOR_LAUNCH" >> "$LOG"
        dispatch_clean_supervisor "$TS"
    fi
fi

release_lock

exit 0
