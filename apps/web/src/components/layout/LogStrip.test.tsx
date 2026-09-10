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
const sampleLogs = [
  { ts: '12:00:01', tone: 'flat', text: 'fetch.py: pull ok' },
  { ts: '12:00:02', tone: 'warn', text: 'features.py: lag spike' },
  { ts: '12:00:03', tone: 'ok', text: 'strategy.py: signal sent' },
  { ts: '12:00:04', tone: 'ok', text: 'broker.py: order filled' },
  { ts: '12:00:05', tone: 'err', text: 'risk.py: position rejected' },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('LogStrip', () => {
  afterEach(() => {
    server.resetHandlers();
  });

  it('renders log entries from mock data', async () => {
    server.use(
      http.get('/api/logs', () => HttpResponse.json(sampleLogs)),
    );
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
    server.use(
      http.get('/api/logs', () => HttpResponse.json(sampleLogs)),
    );
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
    server.use(
      http.get('/api/logs', () => HttpResponse.json(sampleLogs)),
    );
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
    server.use(
      http.get('/api/logs', () => HttpResponse.json(sampleLogs)),
    );
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

  it('renders nothing while the logs query is still loading', async () => {
    // Force the query to never resolve; the strip should return null
    // instead of an empty container.
    server.use(
      http.get('/api/logs', () => new Promise(() => {})),
    );
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    // The query is in flight → data is undefined → component returns null.
    expect(container.firstChild).toBeNull();
  });
});
