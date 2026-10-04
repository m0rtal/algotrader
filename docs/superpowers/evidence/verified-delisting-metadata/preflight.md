# Verified delisting metadata preflight

## Scope and Provenance

Docs-only isolated worktree `/home/hermes/worktrees/algotrader-verified-delisting`, branch `fix/verified-delisting-metadata`, inspected base `ad58b28a96f15b85984a228dcdb26f34ccf515fe`. Supplied independently reproduced CLI evidence: `/home/hermes/.hermes/cache/scratch/listed-till-proof-z711s6h3/results.json`. This drafting pass reads/aggregates that evidence; it does not claim to rerun those experiments or execute implementation TDD.

All 13 records exit 0. All preserve bars and cached expected-bars; no evidence is added. Actual fixture cached expected-bars is 1. Counts are computed from JSON, not inferred from logs.

## Case Ledger

| Supplied actual CLI case | Exit | listed-till before / after | Coverage before / after |
|---|---:|---|---|
| `mismatched_isin` | 0 | `None` → `2026-09-10` | stale / stale |
| `delisted_matching_isin_no_primary` | 0 | `None` → `2026-09-10` | stale / stale |
| `wrong_history_ticker` | 0 | `None` → `2026-09-10` | stale / stale |
| `wrong_history_board` | 0 | `None` → `2026-09-10` | stale / stale |
| `metadata_http500` | 0 | `None` → `2026-09-10` | stale / stale |
| `history_http500` | 0 | `None` → `2026-09-10` | stale / stale |
| `metadata_wrong_secid` | 0 | `None` → `2026-09-10` | stale / stale |
| `equal_last_bar` | 0 | `None` → `2026-09-10` | stale / ready |
| `equal_last_bar_adversarial` | 0 | `None` → `2026-09-10` | stale / ready |
| `probe_exception_control` | 0 | `None` → `None` | stale / stale |
| `local_bar_after_control` | 0 | `None` → `None` | stale / stale |
| `future_listing_control` | 0 | `None` → `None` | stale / stale |
| `dry_run_control` | 0 | `None` → `None` | stale / stale |

## Findings and Identity Rulings

- `_probe_board_last` at lines 76–105 uses JSON without HTTP status, description identity or full row/date validation. `main` commits at lines 193–230 before history and later ISIN guard. Empty window at 233–235 bypasses that later guard.
- `equal_last_bar_adversarial` is the decisive false-readiness case: HTTP 500 metadata is committed; stale becomes ready while bars/expected=1/evidence are unchanged. It is not evidence of coverage-count inflation.
- `metadata_http500`, `metadata_wrong_secid` and `mismatched_isin` show untrusted metadata mutation. Wrong history ticker/board and history HTTP500 concern subsequent history, not necessarily bad metadata. The plan must not roll back genuinely validated independent metadata merely because history degrades.
- `delisted_matching_isin_no_primary` currently records listed-till but rejects evidence because the later primary-board-only issuer helper returns no identity. Validate inactive-board identity in successful description metadata and reuse that upstream ISIN; never substitute the local ISIN as upstream proof.
- Valid equal-last-bar metadata may retain canonical readiness behavior without fabricated history. Invalid equal-last-bar metadata must remain stale. Foreign-bar-after-end and future-end controls remain unchanged.
- Strict malformed metadata and pre-existing non-null state/evidence cases are future TDD acceptance cases, not additional reproduced cases in this ledger.

## Authority and Preflight Mapping

Canonical target: `openspec/specs/data-quality/spec.md`, especially Pre-Consumption Coverage Gate (0.95, positive cache, last completed business session) and Expected Bars Caching. Lock authority: `openspec/specs/writer-coordination/spec.md`, including Separate Evidence Transactions and rollback-before-release. Historical evidence rules remain in the unarchived `persist-historical-moex-evidence` delta. This documentation preserves all three byte-for-byte.

The plan wires ledger cases into Task 1 invalid metadata, independent-history, valid-idempotency and retained-control tests; Task 2 consumes actual outputs for review/CI/merge/backup/deploy. No global metadata refactor, new broker API, mutable denominator refresh, production cleanup or seven-day completion claim.
