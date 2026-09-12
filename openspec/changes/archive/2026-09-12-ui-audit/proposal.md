# Change: ui-audit

## Why

Static design audit of `apps/web/` on 2026-09-12 surfaced two critical
WCAG 2.2 AA violations and one design-system integrity gap that is
visibly breaking BackfillTab. This change groups the remediation into a
single capability so the fixes ship together and stay paired to the
audit report.

Source report: `openspec/changes/2026-09-12-ui-audit/design-audit.md`.

## What changes

- **Token system**: add 6 unprefixed CSS var aliases in `globals.css`
  so existing `bg-[var(--card)]` / `text-[var(--muted-foreground)]`
  classes resolve (BackfillTab, Settings sections, TickerDrilldown).
- **Contrast**: bump `--color-text-dim` and `--color-accent` to clear
  WCAG AA on `bg` / `surface`.
- **Keyboard focus**: add a global `focus-visible` ring utility; add
  `type="button"` to the 8 buttons missing it.
- **ARIA**: `Toast` gains `aria-live="polite"`; `TickerDrilldown` gains
  `role="dialog"` + `aria-modal` + `aria-labelledby`; section headers
  migrate from `<div>` to `<h2>`/`<h3>`.
- **Empty/error states**: Trades, Portfolio, Backtest tabs gain the
  standard 3-state block (loading / error / empty).
- **Confirmation**: BackfillTab inline dialog replaced with
  `<ConfirmDialog danger />`; opens only after `pending` count is
  resolved.
- **Motion**: `animate-pulse` on Topbar status dot suppressed under
  `prefers-reduced-motion: reduce`.
- **Skeleton**: `LogStrip` shows a skeleton row on first fetch instead
  of `return null`.

## Impact

- Affected specs: `frontend-shell` (the only currently-active UI spec).
- Affected code: `apps/web/src/styles/globals.css`,
  `apps/web/src/components/layout/LogStrip.tsx`,
  `apps/web/src/components/layout/Sidebar.tsx`,
  `apps/web/src/components/layout/RightRail.tsx`,
  `apps/web/src/components/charts/TickerDrilldown.tsx`,
  `apps/web/src/features/backfill/BackfillTab.tsx`,
  `apps/web/src/features/settings/SettingsTab.tsx`,
  `apps/web/src/features/settings/Toast.tsx`,
  `apps/web/src/features/settings/ConfirmDialog.tsx`,
  `apps/web/src/features/signals/SignalsTab.tsx`,
  `apps/web/src/features/trades/TradesTab.tsx`,
  `apps/web/src/features/portfolio/PortfolioTab.tsx`,
  `apps/web/src/features/backtest/BacktestTab.tsx`.
- No backend changes; no API changes; no MSW handler changes.
- Test impact: existing tests stay green by construction (no API change,
  no public component API change). New behaviour to verify with axe-core
  in CI is captured in tasks.

## Out of scope

- New components (e.g. a shared `<Skeleton>`, `<ErrorState>`,
  `<EmptyState>`) — this change adds the patterns in-place and extracts
  only when a second call site needs them (YAGNI / ponytail).
- Chart component accessibility beyond `TickerDrilldown` shell — chart
  internals (EquityCurve, Sparkline) are stub SVG and will get their own
  audit pass.
- Routing / navigation changes.
- Theming-light spec work (separate roadmap item).
