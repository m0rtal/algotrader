#!/usr/bin/env bash
# install_cron_liveness.sh — install the algotrader liveness watchdog.
# Idempotent: only adds the entry if not present.
#
# What it does:
#   Adds a `*/2 * * * *` cron entry that runs
#   scripts/cron_liveness_check.sh every 2 minutes. The script reads
#   pipeline_heartbeat.updated_at from the prod DB and SIGKILLs the
#   live worker if the heartbeat is older than 5 minutes (stale).
#
# Why not commit this to git under ops/cron/?
#   crontab is per-host; the entry must match the exact path the script
#   lives at on THIS host. Keeping the install step a manual operator
#   action (this script) lets the same branch deploy to multiple hosts
#   without one host's cron polluting another's.
#
# Run once per host:
#   bash scripts/install_cron_liveness.sh
# Verify:
#   crontab -l | grep cron_liveness_check
#
# To uninstall:
#   crontab -l | grep -v 'cron_liveness_check' | crontab -

set -u
CRON_LINE="*/2 * * * * bash /home/hermes/algotrader/scripts/cron_liveness_check.sh >> /home/hermes/.hermes/logs/algotrader-liveness-cron.log 2>&1"

current="$(crontab -l 2>/dev/null || true)"
if echo "$current" | grep -F 'cron_liveness_check.sh' >/dev/null; then
    echo "Already installed."
    exit 0
fi
( echo "$current"; echo "$CRON_LINE" ) | crontab -
echo "Installed: $CRON_LINE"
