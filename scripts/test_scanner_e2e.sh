#!/usr/bin/env bash
# End-to-end credential scanner smoke test.
# Runs the scanner against a synthetic staged credential and checks exit code.
# Used in scripts/test_scan_credentials.py and ad-hoc verification.
set -e

TMP=$(mktemp -d)
cd "$TMP"

git init -q
git config user.email "test@example.com"
git config user.name "Test"

# Build a fake PAT at runtime so static scanners don't redact the literal.
PAT_PREFIX="ghp_"
PAT_SUFFIX=$(printf 'Z%.0s' {1..36})
PAT="${PAT_PREFIX}${PAT_SUFFIX}"

cat > creds.txt << EOF
api_key="${PAT}"
EOF

git add creds.txt

# Run scanner — expect exit 1 (finding)
if python3 "$1" 2>/dev/null; then
    echo "FAIL: scanner did not detect fake PAT"
    rm -rf "$TMP"
    exit 1
else
    echo "OK: scanner detected fake PAT"
    rm -rf "$TMP"
    exit 0
fi
