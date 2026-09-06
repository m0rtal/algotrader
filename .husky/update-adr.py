#!/usr/bin/env python3
"""Refresh codebase-memory ADR after structural changes.

Runs in `post-commit` (not pre-commit): ADR is documentation, must not block commits.
Triggers only on structural changes: new/removed files under apps/, packages/,
root config, or openspec/. Pulls current architecture from codebase-memory
itself and rewrites the ADR.

Note: this script writes the ADR via the codebase-memory MCP server using
the same JSON-RPC protocol as refresh-codebase-memory.py.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path("/home/hermes/algotrader").resolve()
MCP_BIN = "/home/hermes/.local/bin/codebase-memory-mcp"
PROJECT = "home-hermes-algotrader"
ADR_SECTIONS = ("PURPOSE", "STACK", "ARCHITECTURE", "PATTERNS", "TRADEOFFS", "PHILOSOPHY")
STRUCTURAL_GLOBS = (
    "apps/", "packages/", "openspec/", "tsconfig.base.json", "pnpm-workspace.yaml",
    "apps/web/src/", "apps/web/package.json", "package.json",
)


def diff_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD~1..HEAD"], cwd=REPO, text=True,
        )
    except subprocess.CalledProcessError:
        out = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=REPO, text=True,
        )
    return [f for f in out.splitlines() if f]


def is_structural(files: list[str]) -> bool:
    return any(
        any(f.startswith(prefix) or f == prefix for prefix in STRUCTURAL_GLOBS)
        for f in files
    )


def call_mcp(tool: str, args: dict) -> dict:
    req = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
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
    proc.wait(timeout=120)
    if not raw:
        return {"isError": True, "error": "empty MCP response"}
    try:
        envelope = json.loads(raw.splitlines()[-1])
    except json.JSONDecodeError as e:
        return {"isError": True, "error": f"json decode: {e}"}
    if "error" in envelope:
        return {"isError": True, "error": envelope["error"]}
    result = envelope.get("result", {})
    if result.get("isError"):
        return {"isError": True, "error": result.get("content")}
    content = result.get("content", [])
    if content and content[0].get("type") == "text":
        try:
            return json.loads(content[0]["text"])
        except json.JSONDecodeError:
            return {"isError": True, "error": "content not JSON"}
    return result


def detect_apps_and_packages() -> tuple[list[str], list[str]]:
    apps = sorted(p.name for p in (REPO / "apps").iterdir() if p.is_dir()) if (REPO / "apps").exists() else []
    pkgs = sorted(p.name for p in (REPO / "packages").iterdir() if p.is_dir()) if (REPO / "packages").exists() else []
    return apps, pkgs


def build_adr(files: list[str]) -> str:
    apps, pkgs = detect_apps_and_packages()
    # Re-read the existing ADR and only update the lines that need updating.
    # For now, rebuild the whole ADR (acceptable cost; ADR is short).
    return f"""# Algotrader — Architecture Decision Record

## PURPOSE
Self-hosted algorithmic trading platform for MOEX (Moscow Exchange). Daily-bar strategy with cross-sectional ML ranking, HMM regime detection, and Tinkoff broker integration. Single-user desktop deployment today; multi-user is a future concern, not a v1 constraint. Project is spec-driven via OpenSpec; every capability lives at `openspec/specs/<name>/spec.md` as canonical documentation that survives code churn.

## STACK
- **Frontend (apps/web):** React 19 + Vite 8 + TypeScript 5.7 (strict). Wouter for routing (single dashboard route). TanStack Query v5 for server state, Zustand 5 for client state. Tailwind CSS 4 with `@theme` design tokens. TradingView Lightweight Charts 5 for all financial visualizations. MSW 2 for offline dev mocking.
- **Shared types (packages/shared):** Zod schemas + TypeScript types, re-exported via `@algotrader/shared` workspace package.
- **Backend (apps/api, planned):** FastAPI + tinkoff-python. Separate OpenSpec change.
- **Data (planned):** parquet files on disk + DuckDB for analytics.
- **ML (planned):** XGBoost for cross-sectional ranking, hmmlearn for regime HMM.
- **Spec workflow:** OpenSpec 1.12. `openspec/specs/<capability>/spec.md` canonical. `openspec/changes/<id>/` for active work. `archive/<date>-<id>/` after implementation.

## ARCHITECTURE
Monorepo with pnpm workspaces. Apps: {", ".join(apps) or "(none yet)"}. Packages: {", ".join(pkgs) or "(none yet)"}. Frontend is feature-based, not layered — every feature folder (`features/<name>/`) is self-contained and communicates only via `lib/` (server state) and `stores/` (client state).

```
apps/web/src/
  app/          Bootstrap only. main.tsx (MSW gate), App.tsx, providers.tsx (QueryClient), router.tsx.
  pages/        Route targets. Only Dashboard.tsx for v1 (single route + 5 in-page tabs).
  features/     Feature modules: signals, trades, portfolio, backtest, storage, regime, model.
  components/   Shared layout (Topbar, KPIStr, Sidebar, RightRail, LogStrip) + charts (EquityCurve, Sparkline, TickerDrilldown).
  lib/          Pure utilities. api (fetch wrapper, ApiError class), format (RUB/pct/date), hooks (typed TanStack Query wrappers), queryClient.
  stores/       Zustand. Only uiStore (activeTab, selectedTicker).
  mocks/        MSW. data.ts (14kB), handlers.ts (11 endpoints), browser.ts (dev), server.ts (test).
  styles/       globals.css with Tailwind 4 @theme tokens matching dashboard.html palette.
  test/         Vitest setup: ResizeObserver polyfill, MSW server start.
packages/shared/src/
  index.ts      Zod schemas + types. Source of truth for API contracts.
```

## PATTERNS
- **Spec-driven changes:** Every new capability starts as an OpenSpec change (proposal + design + tasks + spec delta). Implementation follows the spec; archive is the git commit point.
- **MSW for offline dev:** Service worker intercepts `/api/*` in dev only (`if (import.meta.env.DEV)`). Real backend is swapped in by changing the API base URL.
- **Zod-validated API responses:** Every TanStack Query hook calls `Schema.parse(data)` so type mismatches throw at runtime, not silently.
- **Feature-isolated components:** Each `features/<name>/` folder owns its tab content. Cross-feature state goes through stores (Zustand) or server state (TanStack Query).
- **Test-driven development (TDD):** Coverage threshold enforced at 95% for all four metrics. Vitest config fails the build if any metric drops below threshold.
- **Lightweight Charts wrapper pattern:** All chart rendering goes through `components/charts/*.tsx` wrappers. Tests mock the entire `lightweight-charts` module.
- **Path aliases:** `@/*` for src, `@features/*`, `@components/*`, `@lib/*`, `@stores/*`, `@pages/*`. Both Vite and Vitest resolve them.
- **Auto-refresh ADR + index:** `pre-commit` reindexes codebase-memory; `post-commit` rewrites ADR when structural changes detected.

## TRADEOFFS
- **No routing library, single route:** Wouter is a 1.5KB router. We have exactly one route (`/`) and use in-page tabs. React Router would be overkill; TanStack Router would be premature.
- **MSW over real API for v1:** Lets the frontend be developed and tested without a backend. Mitigated by Zod schemas (handler returns the same shape as real API).
- **Tailwind 4 with hand-rolled components, not shadcn init:** shadcn CLI is interactive and would conflict with feature-based structure. Copy patterns (Radix a11y, CVA) without CLI.
- **TypeScript strict + noUncheckedIndexedAccess:** Catches more bugs at compile time. Cost: explicit `!` non-null assertions for array access.
- **Lightweight Charts v5 API:** Uses `chart.addSeries(AreaSeries, options)` instead of v4's `chart.addAreaSeries()`. Current API.
- **No shadcn/ui installation:** Built equivalent components inline. Reconsider if date pickers / dropdowns from Radix needed.
- **Coverage threshold at 95% (not 100%):** Ponytail's 100% would be over-engineering. 95% catches real gaps without forcing defensive tests for unreachable code.

## PHILOSOPHY
- **Ponytail:** Shortest working diff wins. Delete over add. YAGNI aggressively. Stdlib and native first, deps last.
- **Caveman:** Compress output. Dashboard is dense; spec is detailed; code should be too — but only what earns its place.
- **TDD (red-green-refactor):** Test first, watch it fail, then write minimal code. Coverage threshold is a hard rule, not aspirational. Live money demands it.
- **OpenSpec discipline:** Specs live with the code, not in a separate wiki. Changes are reviewable in PRs.
- **Ship the laziest working version, then expand:** Single dashboard.html → React + Vite + TanStack Query + MSW. Each step testable, deployable, reversible.
- **No premature optimization:** Bundle 495KB (151KB gzip) is 200KB over the original 300KB target. Don't fix until real users care. Route-based code splitting when needed.
"""


def main() -> int:
    files = diff_files()
    if not is_structural(files):
        print(f"ADR: skip (no structural changes in {len(files)} files)")
        return 0
    print(f"ADR: structural change detected ({sum(1 for f in files if is_structural([f]))} files), rewriting...")
    adr_content = build_adr(files)
    res = call_mcp(
        "manage_adr",
        {"project": PROJECT, "mode": "update", "content": adr_content},
    )
    if res.get("isError"):
        print(f"ADR: FAILED: {res.get('error')}", file=sys.stderr)
        return 1
    status = res.get("status", "unknown")
    print(f"ADR: updated ({status})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
