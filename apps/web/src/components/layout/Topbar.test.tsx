import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { Topbar } from '@components/layout/Topbar';

// Topbar now mounts <PipelineStatusBanner /> which uses the React
// Query `useBackfillStatus()` hook, so the test needs a
// QueryClientProvider. The hook fetches
// /api/admin/backfill/status every 5s; in test mode the dev MSW
// handler short-circuits with 503 (see mocks/passthrough.ts) and the
// banner renders nothing in that case — no banner DOM is asserted
// here, only the pre-existing brand / sandbox / settings link.

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

describe('Topbar', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the brand and a Sandbox label', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Topbar />
      </Wrapper>,
    );
    expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
  });

  it('renders a link to settings', () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Topbar />
      </Wrapper>,
    );
    const link = screen.getByRole('link', { name: /settings/i });
    expect(link.getAttribute('href')).toBe('/settings');
  });
});
