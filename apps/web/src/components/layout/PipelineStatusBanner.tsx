import { useBackfillStatus } from '@features/backfill/hooks';

// PipelineStatusBanner — surfaces the data-pipeline liveness state from
// GET /api/admin/backfill/status (polled every 5s by useBackfillStatus).
// Spec: openspec/changes/archive/2026-09-27-autonomous-data-pipeline/
//       specs/data-quality/spec.md#requirement:ui-surfaces-stale-pipeline-immediately
//
// Severity tiers:
//   - last_cycle_age_seconds == null                  → render nothing
//                                                        (no cycle yet)
//   - last_cycle_age_seconds < 14400s (4h)             → render nothing
//                                                        (green path)
//   - 14400s ≤ age < 86400s (4–24h)                    → yellow, role="status"
//                                                        aria-live="polite"
//   - age ≥ 86400s (24h)                              → red, role="alert"
//                                                        aria-live="assertive"
//   - network / 5xx error                              → red "Cannot reach"
//
// The banner is in normal document flow above the Topbar row and is
// NOT a modal — it does not block clicks. Accessibility: yellow uses
// the polite live region so screen readers announce it without
// interrupting; red uses assertive so it preempts current speech.

// 4 hours — under this age the pipeline is considered fresh.
const FRESH_THRESHOLD_SECONDS = 4 * 60 * 60; // 14_400
// 24 hours — at/over this age the pipeline is treated as fully stopped.
const STOPPED_THRESHOLD_SECONDS = 24 * 60 * 60; // 86_400

function formatAgeMinutes(seconds: number): string {
  // Minutes/hours resolution matches the spec wording ("X minutes since
  // last refresh" / "X hours ago"). Whole-number formatting keeps the
  // text predictable for screen readers and avoids jitter as the age
  // crosses the minute boundary.
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} minutes`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hours`;
}

type BannerProps = { tone: 'yellow' | 'red'; message: string };

function Banner({ tone, message }: BannerProps) {
  // Solid colour from the design tokens so contrast stays consistent
  // with the rest of the topbar (color-red / color-amber). Yellow uses
  // the amber token — the spec calls it "yellow" but the design system
  // names the closest match --color-amber. Compact h-8 so it doesn't
  // push the rest of the topbar down further than necessary.
  const toneClass =
    tone === 'red'
      ? 'bg-red-soft text-red border-red/40'
      : 'bg-amber/15 text-amber border-amber/40';
  return (
    <div
      role={tone === 'red' ? 'alert' : 'status'}
      aria-live={tone === 'red' ? 'assertive' : 'polite'}
      data-testid="pipeline-status-banner"
      data-tone={tone}
      className={`flex items-center gap-2 px-3 sm:px-5 h-8 text-xs border-b border-border ${toneClass}`}
    >
      <span aria-hidden="true" className="font-semibold uppercase tracking-wider">
        {tone === 'red' ? '⛔ Pipeline' : '⚠ Pipeline'}
      </span>
      <span className="truncate">{message}</span>
    </div>
  );
}

export function PipelineStatusBanner() {
  // Reuses the same React Query key the rest of the app uses for the
  // backfill status endpoint (see apps/web/src/features/backfill/hooks.ts),
  // so no duplicate fetches — TanStack dedupes by queryKey.
  const query = useBackfillStatus();

  // Treat any non-2xx / network failure the same way: red "Cannot
  // reach" banner. We do not try to parse the body — the only thing
  // the operator needs to know is that the status API is unavailable.
  if (query.isError) {
    return <Banner tone="red" message="Cannot reach data pipeline status API" />;
  }

  // While the query is still loading we render nothing — flashing a red
  // banner for a few hundred milliseconds on first paint would be
  // misleading noise. The 5s polling interval keeps the staleness low.
  const data = query.data;
  if (!data) return null;

  // last_cycle_age_seconds is `number | null` in the type but a fresh
  // response from an older backend (or a test fixture that has not been
  // updated yet) can omit the field, in which case JSON.parse leaves
  // it undefined. Treat both null and undefined as "no cycle yet" so
  // older test fixtures don't suddenly start showing a yellow banner.
  const age = data.last_cycle_age_seconds;
  if (age === null || age === undefined) return null;
  // Fresh (< 4h) → green path, banner hidden.
  if (age < FRESH_THRESHOLD_SECONDS) return null;

  if (age >= STOPPED_THRESHOLD_SECONDS) {
    return (
      <Banner
        tone="red"
        message={`Data refresh has stopped — last successful cycle was ${formatAgeMinutes(age)} ago`}
      />
    );
  }

  return (
    <Banner tone="yellow" message={`Data is stale (${formatAgeMinutes(age)} since last refresh)`} />
  );
}
