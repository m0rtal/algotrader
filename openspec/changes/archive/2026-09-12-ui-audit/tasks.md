# Tasks

## 1. Token aliases + contrast bump [VALIDATED]

- [ ] 1.1 Add to `apps/web/src/styles/globals.css` `@theme {}`:
      `--background: var(--color-bg); --card: var(--color-surface);`
      `--muted: var(--color-border-soft);`
      `--muted-foreground: var(--color-text-muted);`
      `--border: var(--color-border); --accent: var(--color-accent);`
- [ ] 1.2 Bump `--color-text-dim` from `#5a6075` to `#888ea0`
      (≥4.6:1 on bg).
- [ ] 1.3 Bump `--color-accent` from `#5d5fef` to `#7a82ff`
      (≥4.5:1 on bg).
- [ ] 1.4 Extract `--color-green-soft: rgba(38,166,154,0.15)`,
      `--color-red-soft: rgba(239,83,80,0.15)`,
      `--color-text-soft: rgba(138,145,163,0.15)`.
- [ ] 1.5 Replace hardcoded `rgba(...)` in
      `apps/web/src/features/signals/SignalsTab.tsx:60-63` with the new
      token classes.

## 2. Keyboard focus + button hygiene [VALIDATED]

- [ ] 2.1 Add a global focus-visible ring utility to `globals.css`
      `@layer base {}`:
      `button:focus-visible, [role="button"]:focus-visible, a:focus-visible`
      → `outline: 2px solid var(--color-accent); outline-offset: 2px;`
- [ ] 2.2 Add `type="button"` to the 8 buttons that lack it:
      `pages/Dashboard.tsx:41`,
      `components/charts/TickerDrilldown.tsx:42`,
      `components/layout/Sidebar.tsx:40`,
      `features/backfill/BackfillTab.tsx:219,226,233,307,313`,
      `features/signals/SignalsTab.tsx:52`.

## 3. ARIA + headings [GROUNDED — needs axe-core to validate]

- [ ] 3.1 `features/settings/Toast.tsx`: add `aria-live="polite"`
      `aria-atomic="true"` to the root `<div role="status">`.
- [ ] 3.2 `components/charts/TickerDrilldown.tsx`: add
      `role="dialog"`, `aria-modal="true"`,
      `aria-labelledby={titleId}` to the modal panel; `id` the title span.
- [ ] 3.3 Replace uppercase-tracked `<div>` section headers with
      `<h2>`/`<h3>` in: `components/layout/Sidebar.tsx:14,31`,
      `components/layout/RightRail.tsx:11,22,37`,
      `features/backfill/BackfillTab.tsx:168,254`.

## 4. Loading / empty / error [GROUNDED]

- [ ] 4.1 Add the standard 3-state block to
      `features/trades/TradesTab.tsx`,
      `features/portfolio/PortfolioTab.tsx`,
      `features/backtest/BacktestTab.tsx` (loading / error / empty paths
      mirroring SignalsTab / StorageTab).
- [ ] 4.2 `components/layout/LogStrip.tsx`: replace
      `if (!data) return null` with a skeleton row showing
      "—" or three dim dashes, sized like the eventual log lines.

## 5. Confirm dialog consolidation [GROUNDED]

- [ ] 5.1 Replace inline confirm dialog in
      `features/backfill/BackfillTab.tsx:294-325` with
      `<ConfirmDialog danger title=... body=...>`.
- [ ] 5.2 Gate the "Reset metadata" button on pending count resolved:
      `disabled={!pending.data}` so the dialog never opens with `?`
      as the row count.

## 6. Motion [VALIDATED]

- [ ] 6.1 `apps/web/src/styles/globals.css`: add
      `@media (prefers-reduced-motion: reduce) { .animate-pulse { animation: none; } }`
      (or `motion-reduce:animate-none` on Topbar's pulse span).

## 7. Validation [VALIDATED / GROUNDED]

- [ ] 7.1 Add `axe-core` programmatic check script
      `apps/web/scripts/axe-check.mjs` that boots Vite preview, runs
      Playwright + axe-core against the 3 main routes (`/`, `/settings`,
      drilldown open), fails the build on any Critical/Serious violation.
- [ ] 7.2 Add contrast spot-check script
      `apps/web/scripts/contrast-check.mjs` that parses `@theme` tokens
      from `globals.css` and asserts every fg/bg pair in the audit matrix
      ≥ WCAG AA.
- [ ] 7.3 Wire both into `package.json` as `pnpm lint:a11y` and run
      in CI.

## 8. Tests [GROUNDED]

- [ ] 8.1 Update vitest snapshots/assertions for
      `BackfillTab.test.tsx` (token swap changes className strings).
- [ ] 8.2 Add unit test for ConfirmDialog Escape handler and focus
      trap (currently absent).

## Notes

- Items marked [VALIDATED] need no browser run; items [GROUNDED] become
  [VALIDATED] once 7.1 is wired and passes.
- Order matters: 1.x before 2.x (so the new accent colour is the
  focus ring colour); 5.x after 1.1 (so ConfirmDialog inherits the
  token aliases).
