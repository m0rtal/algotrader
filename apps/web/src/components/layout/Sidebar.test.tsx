import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { Sidebar } from '@components/layout/Sidebar';
import { useUiStore } from '@stores/uiStore';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('Sidebar', () => {
  it('renders the regime section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
    });
  });

  it('renders the universe section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Universe/)).toBeInTheDocument();
    });
  });

  it('displays a list of tickers', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('SBER').length).toBeGreaterThan(0);
      expect(screen.getAllByText('GAZP').length).toBeGreaterThan(0);
    });
  });

  it('shows the regime confidence value', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/conf 0\.78/)).toBeInTheDocument();
    });
  });

  it('shows HMM info and since date', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/HMM/)).toBeInTheDocument();
    });
  });

  it('clicking a ticker updates the uiStore selectedTicker', async () => {
    const Wrapper = makeWrapper();
    const { getByText } = render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('YNDX')).toBeInTheDocument();
    });
    const before = useUiStore.getState().selectedTicker;
    expect(before).toBeNull();
    fireEvent.click(getByText('YNDX'));
    expect(useUiStore.getState().selectedTicker).toBe('YNDX');
  });

  it('clicking different tickers replaces the selected one', async () => {
    const Wrapper = makeWrapper();
    const { getByText } = render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('LKOH')).toBeInTheDocument();
    });
    fireEvent.click(getByText('SBER'));
    expect(useUiStore.getState().selectedTicker).toBe('SBER');
    fireEvent.click(getByText('LKOH'));
    expect(useUiStore.getState().selectedTicker).toBe('LKOH');
  });

  it('renders without crashing when regime returns non-trend state', async () => {
    const { server } = await import('../../mocks/server');
    const { http, HttpResponse } = await import('msw');
    server.use(
      http.get('/api/regime', () =>
        HttpResponse.json({
          state: 'range',
          confidence: 0.5,
          imoexChange: -0.3,
          volatility20d: 12,
          breadth: 0.6,
          sinceDate: '2026-09-05',
        }),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('range')).toBeInTheDocument();
      expect(screen.getByText(/-0\.3%/)).toBeInTheDocument();
    });
  });

  it('falls back to empty ticker list when tickers query returns null', async () => {
    // Override the tickers endpoint to return an error so useTickers has no data.
    // This exercises the `tickers ?? []` fallback branch.
    const { server } = await import('../../mocks/server');
    const { http, HttpResponse } = await import('msw');
    server.use(http.get('/api/tickers', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Sidebar />
      </Wrapper>,
    );
    // Component still renders the regime section and the universe title
    await waitFor(() => {
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
      expect(screen.getByText(/Universe/)).toBeInTheDocument();
    });
  });
});
