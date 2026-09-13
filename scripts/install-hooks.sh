#!/usr/bin/env bash
# scripts/install-hooks.sh
# Set core.hooksPath so the hook is active.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
git config core.hooksPath "$SCRIPT_DIR/git-hooks"
chmod +x "$SCRIPT_DIR/git-hooks/pre-push"
echo "✓ Installed pre-push hook from $SCRIPT_DIR/git-hooks/"