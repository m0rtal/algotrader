import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TickerDrilldown } from '@components/charts/TickerDrilldown';
import { useUiStore } from '@stores/uiStore';

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

describe('TickerDrilldown', () => {
  beforeEach(() => {
    useUiStore.setState({ selectedTicker: null });
  });

  it('renders nothing when no ticker is selected', () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    expect(container.querySelector('.fixed')).toBeNull();
  });

  it('renders the modal when a ticker is selected', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    // wait for ticker data to load
    await waitFor(() => {
      expect(screen.getByText(/data\/bars\/SBER\.parquet/)).toBeInTheDocument();
    });
  });

  it('shows the ticker name in the header', async () => {
    useUiStore.setState({ selectedTicker: 'GAZP' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/Газпром/)).toBeInTheDocument();
    });
  });

  it('displays the source path', async () => {
    useUiStore.setState({ selectedTicker: 'YNDX' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/data\/bars\/YNDX\.parquet/)).toBeInTheDocument();
    });
  });

  it('displays the MOEX ISS source', async () => {
    useUiStore.setState({ selectedTicker: 'YNDX' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/MOEX ISS/)).toBeInTheDocument();
    });
  });

  it('shows the close button', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('✕')).toBeInTheDocument();
    });
  });

  it('close button clears the selected ticker', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('✕')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('✕'));
    expect(useUiStore.getState().selectedTicker).toBeNull();
  });

  it('renders sector label', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Banks')).toBeInTheDocument();
    });
  });

  it('displays the bar count from API', async () => {
    useUiStore.setState({ selectedTicker: 'SBER' });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TickerDrilldown />
      </Wrapper>,
    );
    await waitFor(() => {
      // mock returns 30 bars
      expect(screen.getByText('30')).toBeInTheDocument();
    });
  });
});
