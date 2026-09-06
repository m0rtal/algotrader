#!/usr/bin/env python3
"""Refresh codebase-memory index after commits that touch source code.

Speaks JSON-RPC over stdio to the codebase-memory MCP server, mirroring
what Hermes does internally. Skips fast if the diff is docs/config only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/hermes/algotrader").resolve()
MCP_BIN = "/home/hermes/.local/bin/codebase-memory-mcp"
PROJECT = "home-hermes-algotrader"
SOURCE_GLOBS = (".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html")


def diff_files() -> list[str]:
    """Files changed in HEAD~1..HEAD (or working tree if no prev commit)."""
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"],
            cwd=REPO, text=True,
        )
    except subprocess.CalledProcessError:
        # First commit or detached HEAD
        out = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=REPO, text=True,
        )
    return [f for f in out.splitlines() if f]


def needs_refresh(files: list[str]) -> bool:
    """Refresh only when source code or specs changed."""
    return any(
        f.endswith(SOURCE_GLOBS) or f.startswith("openspec/") or f.endswith(".md") and "spec" in f
        for f in files
    )


def call_mcp(tool: str, args: dict) -> dict:
    """Send a single MCP tools/call request and read the response."""
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    proc = subprocess.Popen(
        [MCP_BIN],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert proc.stdin and proc.stdout
    proc.stdin.write(json.dumps(req).encode())
    proc.stdin.write(b"\n")
    proc.stdin.flush()
    proc.stdin.close()
    raw = proc.stdout.read()
    proc.wait(timeout=180)
    if not raw:
        return {"isError": True, "error": "empty response from MCP server"}
    try:
        envelope = json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError as e:
        return {"isError": True, "error": f"json decode: {e}; raw head: {raw[:200]!r}"}
    if "error" in envelope:
        return {"isError": True, "error": envelope["error"]}
    result = envelope.get("result", {})
    if result.get("isError"):
        return {"isError": True, "error": result.get("content")}
    # MCP wraps tool output in content[0].text as a JSON string
    content = result.get("content", [])
    if content and isinstance(content, list) and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return {"isError": True, "error": "content not JSON", "raw": content[0]["text"][:200]}
    return result


def main() -> int:
    files = diff_files()
    if not needs_refresh(files):
        print(f"codebase-memory: skip (no source changes in {len(files)} files)")
        return 0
    print(f"codebase-memory: reindexing ({len(files)} files changed)...")
    res = call_mcp(
        "index_repository",
        {"repo_path": str(REPO), "mode": "moderate", "name": PROJECT},
    )
    if res.get("isError") or res.get("status") != "indexed":
        print(f"codebase-memory: FAILED: {res}", file=sys.stderr)
        return 1
    nodes = res.get("nodes", "?")
    edges = res.get("edges", "?")
    skipped = res.get("skipped_count", 0)
    partial = res.get("parse_partial_count", 0)
    warn = f" [{skipped} skipped, {partial} partial]" if (skipped or partial) else ""
    print(f"codebase-memory: OK ({nodes} nodes, {edges} edges){warn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
