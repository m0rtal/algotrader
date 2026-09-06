#!/usr/bin/env python3
"""Pre-commit credential scanner.

Staged file content scanner for secrets (tokens, private keys, API keys).
Runs before commit via .husky/pre-commit (when configured).

Detects:
- GitHub PAT (ghp_, gho_, ghu_, ghs_, ghr_)
- GitLab PAT (glpat-, gplat-)
- AWS Access Key (AKIA...)
- Generic high-entropy strings assigned to suspicious names (token=, api_key=, secret=)
- .env files containing actual values (not just templates)

Exits 0 on clean, 1 on findings. Findings printed to stderr with file:line.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Patterns — case-insensitive where appropriate
PATTERNS = [
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("gitlab_pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9-]{20,}\b")),
    ("private_key", re.compile(r"-----BEGIN .{1,30} KEY-----")),
    ("pan_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),  # rough CC heuristic, high false-positive rate
]

# Names that, if assigned a non-empty value, look suspicious
SUSPECT_NAMES = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|secret|token|password|passwd|access[_-]?key|private[_-]?key)
    \s*[:=]\s*
    ['"]?([A-Za-z0-9+/=_\-]{16,})['"]?
    """
)

# Allowlist — files where these are expected (test fixtures, examples)
ALLOWLIST_FILES = {
    ".env.example",
    ".env.test",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    ".gitignore",
    "README.md",
    "AGENTS.md",
}

# Allowlist paths
ALLOWLIST_PATH_PATTERNS = [
    re.compile(r"apps/web/src/test/mocks/lightweight-charts\.ts$"),  # frontend chart mock
    re.compile(r"\.venv/"),
    re.compile(r"node_modules/"),
    re.compile(r"docs/.*credential-rotation"),
]


def is_allowlisted(path: Path) -> bool:
    path_str = str(path)
    if path.name in ALLOWLIST_FILES:
        return True
    for pattern in ALLOWLIST_PATH_PATTERNS:
        if pattern.search(path_str):
            return True
    return False


def is_template_line(line: str) -> bool:
    """A 'template' line is one with a placeholder, not a real value."""
    placeholders = ["<", ">", "YOUR_", "EXAMPLE_", "xxx", "REPLACE", "PLACEHOLDER", "your-"]
    stripped = line.lower()
    return any(p.lower() in stripped for p in placeholders)


def scan_file(path: Path, content: str) -> list[tuple[str, int, str]]:
    """Return list of (kind, line_no, snippet) findings."""
    findings = []
    for kind, pattern in PATTERNS:
        for m in pattern.finditer(content):
            line_no = content[: m.start()].count("\n") + 1
            snippet = m.group(0)[:40] + ("..." if len(m.group(0)) > 40 else "")
            findings.append((kind, line_no, snippet))
    # Generic suspect-name heuristic (line-level)
    for line_no, line in enumerate(content.splitlines(), start=1):
        if is_template_line(line):
            continue
        if m := SUSPECT_NAMES.search(line):
            value = m.group(1)
            # Skip obvious placeholders / hex with low entropy
            if len(set(value)) < 6:
                continue
            findings.append(("suspect_assignment", line_no, value[:40]))
    return findings


def get_staged_files() -> list[Path]:
    """Return list of staged file paths (added/modified/copied)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=AMC"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [Path(p) for p in result.stdout.splitlines() if p]


def main() -> int:
    paths = get_staged_files()
    if not paths:
        return 0

    total = 0
    for path in paths:
        if is_allowlisted(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except (IsADirectoryError, PermissionError):
            continue
        if not content:
            continue
        findings = scan_file(path, content)
        for kind, line_no, snippet in findings:
            print(f"[CRED] {path}:{line_no} {kind}: {snippet}", file=sys.stderr)
            total += 1

    if total > 0:
        print(f"\nFound {total} potential secret(s) in staged content.", file=sys.stderr)
        print("If these are intentional test fixtures, add the path to ALLOWLIST_FILES in scripts/scan-credentials.py.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
