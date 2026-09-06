import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { SignalsTab } from '@features/signals/SignalsTab';
import { useUiStore } from '@stores/uiStore';

const seriesMock = { setData: vi.fn() };
const chartMock = {
  addSeries: vi.fn(() => seriesMock),
  remove: vi.fn(),
  applyOptions: vi.fn(),
  timeScale: () => ({ fitContent: vi.fn() }),
};
// vi.mock must be at the top before imports
import { vi } from 'vitest';
vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => chartMock),
  AreaSeries: function AreaSeries() {},
}));

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('SignalsTab', () => {
  it('renders the table header', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Сигнал')).toBeInTheDocument();
      expect(screen.getByText('Прогноз 5д')).toBeInTheDocument();
    });
  });

  it('renders rows for each signal', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('SBER').length).toBeGreaterThan(0);
      expect(screen.getAllByText('GAZP').length).toBeGreaterThan(0);
    });
  });

  it('clicking a ticker symbol opens drill-down', async () => {
    useUiStore.setState({ selectedTicker: null });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('YNDX').length).toBeGreaterThan(0);
    });
    const yndxButtons = screen.getAllByText('YNDX');
    fireEvent.click(yndxButtons[0]!);
    expect(useUiStore.getState().selectedTicker).toBe('YNDX');
  });

  it('displays the long/short/hold badge with the right text', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('long').length).toBeGreaterThan(0);
      expect(screen.getAllByText('short').length).toBeGreaterThan(0);
      expect(screen.getAllByText('hold').length).toBeGreaterThan(0);
    });
  });

  it('renders the equity curve section title', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Equity Curve/)).toBeInTheDocument();
    });
  });

  it('shows loading state when query is pending', () => {
    // The query starts in pending state because the response never resolves.
    // We achieve this by NOT setting up MSW for this endpoint, so the request
    // hangs in 'pending' (with onUnhandledRequest: 'bypass' in test setup).
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    // The component returns the loading div while isLoading is true.
    // Since MSW doesn't respond, the query stays pending.
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('shows error state when query fails', async () => {
    const { server } = await import('../../mocks/server');
    const { http, HttpResponse } = await import('msw');
    server.use(http.get('/api/signals', () => new HttpResponse(null, { status: 500 })));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Ошибка загрузки')).toBeInTheDocument();
    });
  });

  it('shows empty state when signal list is empty', async () => {
    const { server } = await import('../../mocks/server');
    const { http, HttpResponse } = await import('msw');
    server.use(http.get('/api/signals', () => HttpResponse.json([])));
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <SignalsTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Нет сигналов')).toBeInTheDocument();
    });
  });
});
