import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PipelineStatusBanner } from '@components/layout/PipelineStatusBanner';
import { server } from '../../mocks/server';

// PipelineStatusBanner renders a visible warning in the Topbar whenever
// the data pipeline has gone stale. The severity tiers come from the
// spec (Section D "UI red banner"):
//
//   last_cycle_age_seconds == null                  → hidden (no cycle yet)
//   last_cycle_age_seconds < 14400s (4h)             → hidden (green path)
//   14400 ≤ age < 86400  (4–24h)                     → yellow, role="status"
//                                                     aria-live="polite"
//   age ≥ 86400s (24h)                              → red, role="alert"
//                                                     aria-live="assertive"
//   network / 5xx error                              → red, "Cannot reach"
//
// Tests exercise the MSW handler in `apps/web/src/mocks/handlers.ts`
// which forwards /api/admin/backfill/status. We install canned
// responses via server.use(http.get(...)) — see Dashboard.test.tsx
// for the established pattern.

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = 'QueryClientWrapper';
  return Wrapper;
}

const sampleStatus = (
  overrides: Partial<{
    state: string;
    last_cycle_age_seconds: number | null;
  }> = {},
) => ({
  state: 'idle',
  run_id: null,
  tickers_done: 0,
  tickers_total: 0,
  total_bars: 0,
  last_run: null,
  last_cycle_age_seconds: null,
  ...overrides,
});

describe('PipelineStatusBanner', () => {
  beforeEach(() => {
    // Default canned response so tests that don't override can still
    // assert "hidden". Individual tests override via server.use().
    server.use(http.get('/api/admin/backfill/status', () => HttpResponse.json(sampleStatus())));
  });
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders nothing when last_cycle_age_seconds is null', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it('renders nothing when the last cycle is fresh (<4h)', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(sampleStatus({ last_cycle_age_seconds: 60 })),
      ),
    );
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.firstChild).toBeNull();
    });
  });

  it('renders a yellow status banner when the cycle is 4–24h old', async () => {
    // 4h + 1s — just over the 4h threshold → yellow.
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(sampleStatus({ last_cycle_age_seconds: 14401 })),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    const banner = await screen.findByRole('status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner.textContent).toMatch(/stale/i);
  });

  it('renders a red alert banner when the cycle is ≥24h old', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(sampleStatus({ last_cycle_age_seconds: 86400 })),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    const banner = await screen.findByRole('alert');
    expect(banner).toHaveAttribute('aria-live', 'assertive');
    expect(banner.textContent).toMatch(/stopped|last successful cycle|hours ago/i);
  });

  it('renders a red alert when the API returns 500', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json({ error: 'backend_error' }, { status: 500 }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    const banner = await screen.findByRole('alert');
    expect(banner).toHaveAttribute('aria-live', 'assertive');
    expect(banner.textContent).toMatch(/cannot reach/i);
  });

  it('does not block clicks behind the banner (no pointer-events:none on host)', async () => {
    server.use(
      http.get('/api/admin/backfill/status', () =>
        HttpResponse.json(sampleStatus({ last_cycle_age_seconds: 86400 })),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PipelineStatusBanner />
      </Wrapper>,
    );
    const banner = await screen.findByRole('alert');
    // The banner should not be a modal — it lives in document flow and
    // must not cover the rest of the UI. We assert the host element has
    // pointer-events: auto (the default), explicitly not 'none'.
    const styles = window.getComputedStyle(banner);
    expect(styles.pointerEvents).not.toBe('none');
  });
});
