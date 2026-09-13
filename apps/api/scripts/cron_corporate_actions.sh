#!/usr/bin/env bash
# apps/api/scripts/cron_corporate_actions.sh
#
# Daily corporate-actions cron, scheduled at 20:30 UTC. Runs:
#   1. snapshot_mode() — append today's face_value/lot_size per secid
#      to `instruments_snapshot`.
#   2. detect_mode()    — diff consecutive snapshots to detect splits
#      and write CorporateActionRow(action_type='split',
#      source='moex_iss_snapshots') rows.
#   3. Tinkoff dividends refresh — same run, separate pipeline phase.
#   4. MOEX ISS dividends refresh — same run, separate pipeline phase.
#
# MOEX ISS publishes events on a T+1 cadence, so a daily tick is plenty
# granular. Universe-sync cron runs at 20:00 UTC; this script starts at
# 20:30 UTC so the `instruments` table is fresh when snapshot_mode()
# resolves figi → secid.
#
# Operator install (one-time, as root or with sudo):
#   ln -sf /home/hermes/algotrader/apps/api/scripts/cron_corporate_actions.sh \
#       /etc/cron.daily/algotrader-corporate-actions
#   chmod +x /home/hermes/algotrader/apps/api/scripts/cron_corporate_actions.sh
#
# Exit code is 0 on full success, non-zero on any phase failure. The cron
# daemon forwards exit codes to /var/log/syslog via cron — operators see
# failures in `journalctl -u cron`.

set -euo pipefail

# Resolve the algotrader repo root and the SQLite DB path. The DB path
# defaults to the ALGOTRADER_DATA_DIR/state.db convention used by the
# FastAPI app; operators can override either via env vars.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$(cd "${HERE}/.." && pwd)"            # this script lives in apps/api/scripts; API_DIR = apps/api
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"        # REPO_ROOT = algotrader (one above apps)

: "${ALGOTRADER_DATA_DIR:=${REPO_ROOT}/apps/api/data}"
: "${ALGOTRADER_DB_PATH:=${ALGOTRADER_DATA_DIR}/state.db}"

mkdir -p "${ALGOTRADER_DATA_DIR}"

log() {
    # Use logger(1) when available (systemd journal / cron syslog) so
    # operators get a timestamped line; fall back to plain stdout for
    # local debugging.
    if command -v logger >/dev/null 2>&1; then
        logger -t algotrader-corporate-actions "$@"
    fi
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)]" "$@"
}

cd "${API_DIR}"

log "starting daily corporate-actions run, db=${ALGOTRADER_DB_PATH}"

# 1+2. Snapshot + split detection (snapshot first, then detect — order
# matters because detect needs at least two snapshots per secid to find
# changes; today's snapshot makes the diff complete).
log "phase=split_snapshot+detect mode=both"
uv run python -m scripts.import_corporate_actions_splits \
    "${ALGOTRADER_DB_PATH}" --mode both

# 3. Tinkoff dividends refresh.
log "phase=tinkoff_dividends"
uv run python -m scripts.import_corporate_actions_tinkoff \
    "${ALGOTRADER_DB_PATH}"

# 4. MOEX ISS dividends cross-check (independent of Tinkoff).
log "phase=moex_dividends"
uv run python -m scripts.import_corporate_actions_moex \
    "${ALGOTRADER_DB_PATH}"

log "completed daily corporate-actions run"