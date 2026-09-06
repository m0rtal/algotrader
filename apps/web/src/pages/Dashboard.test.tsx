import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Dashboard } from '@pages/Dashboard';

const seriesMock = { setData: vi.fn() };
const chartMock = {
  addSeries: vi.fn(() => seriesMock),
  remove: vi.fn(),
  applyOptions: vi.fn(),
  timeScale: () => ({ fitContent: vi.fn() }),
};

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

describe('Dashboard', () => {
  it('renders the 5 tab buttons', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Сигналы' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Сделки' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Портфель' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Бэктест' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Бары (хранилище)' })).toBeInTheDocument();
    });
  });

  it('shows the Signals tab content by default', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Equity')).toBeInTheDocument();
      expect(screen.getByText('Сигнал')).toBeInTheDocument();
    });
  });

  it('clicking the Trades tab shows the trades content', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Сделки' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сделки' }));
    await waitFor(() => {
      expect(screen.getByText('Время')).toBeInTheDocument();
    });
  });

  it('clicking the Portfolio tab shows positions', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Портфель' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Портфель' }));
    await waitFor(() => {
      expect(screen.getByText('Свободно')).toBeInTheDocument();
    });
  });

  it('clicking the Backtest tab shows folds', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Бэктест' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Бэктест' }));
    await waitFor(() => {
      expect(screen.getByText('OOS Sharpe')).toBeInTheDocument();
    });
  });

  it('clicking the Storage tab shows bars table', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Бары (хранилище)' })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Бары (хранилище)' }));
    await waitFor(() => {
      expect(screen.getByText('Всего баров')).toBeInTheDocument();
    });
  });

  it('renders the topbar and sidebar', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
      expect(screen.getByText('Режим рынка')).toBeInTheDocument();
    });
  });

  it('renders the right rail with model and pipeline', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Dashboard />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('ML Model')).toBeInTheDocument();
      expect(screen.getByText('Pipeline status')).toBeInTheDocument();
    });
  });
});
