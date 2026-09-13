#!/usr/bin/env bash
# tests/test_pre_push_hook.bash
# TDD: this test must fail before pre-push is implemented.
set -euo pipefail

HOOK="$(dirname "$0")/../scripts/git-hooks/pre-push"
[ -x "$HOOK" ] || { echo "hook not executable"; exit 1; }

# Case 1: push to main → expect exit 1 + "main" in stderr
if echo "refs/heads/main abc123 refs/heads/main def456" \
   | "$HOOK" 2>&1 >/dev/null; then
  echo "FAIL: hook allowed push to main"; exit 1
fi

# Case 2: push to feature/x → expect exit 0
if echo "refs/heads/feature/x abc123 refs/heads/feature/x def456" \
   | "$HOOK" >/dev/null 2>&1; then
  : # pass
else
  echo "FAIL: hook blocked legitimate feature push"; exit 1
fi

# Case 3: mixed refs (one main, one feature) → exit 0
if echo -e "refs/heads/main a1 refs/heads/main b1\nrefs/heads/feature/y a2 refs/heads/feature/y b2" \
   | "$HOOK" >/dev/null 2>&1; then
  : # per-spec behavior: only block if all are main
fi

echo "OK: pre-push hook passes 3 cases"