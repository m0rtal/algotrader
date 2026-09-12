import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it } from 'vitest';
import { LogStrip } from '@components/layout/LogStrip';
import { server } from '../../mocks/server';

// Test fixtures installed via server.use() — see SignalsTab.test.tsx for
// the established pattern. The dev MSW handlers remain strict passthroughs
// that hit the real backend, so each test opts into its own /api/logs mock.
// LogStrip renders only the first 4 entries — keep err-tone within that
// window so the err-class assertion can find it.
const sampleLogs = [
  { ts: '12:00:01', tone: 'err', text: 'risk.py: position rejected' },
  { ts: '12:00:02', tone: 'flat', text: 'fetch.py: pull ok' },
  { ts: '12:00:03', tone: 'warn', text: 'features.py: lag spike' },
  { ts: '12:00:04', tone: 'ok', text: 'strategy.py: signal sent' },
  { ts: '12:00:05', tone: 'ok', text: 'broker.py: order filled' },
];

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

describe('LogStrip', () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders log entries from mock data', async () => {
    server.use(http.get('/api/logs', () => HttpResponse.json(sampleLogs)));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/fetch\.py/)).toBeInTheDocument();
      expect(screen.getByText(/features\.py/)).toBeInTheDocument();
    });
  });

  it('renders the warn-tone class on a warn log entry', async () => {
    server.use(http.get('/api/logs', () => HttpResponse.json(sampleLogs)));
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelector('.text-amber')).toBeTruthy();
    });
  });

  it('renders the ok-tone class on ok log entries', async () => {
    server.use(http.get('/api/logs', () => HttpResponse.json(sampleLogs)));
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.text-green').length).toBeGreaterThan(0);
    });
  });

  it('renders the err-tone class on error log entries', async () => {
    server.use(http.get('/api/logs', () => HttpResponse.json(sampleLogs)));
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelector('.text-red')).toBeTruthy();
    });
  });

  it('shows a skeleton while the logs query is still loading', async () => {
    // Force the query to never resolve; the strip should render a
    // skeleton placeholder (not return null, not block on the data).
    server.use(http.get('/api/logs', () => new Promise(() => {})));
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    // Skeleton is in the DOM immediately, even before the query settles.
    expect(container.firstChild).not.toBeNull();
    expect(container.querySelector('[data-testid="global-log-strip"]')).toBeTruthy();
  });
});
