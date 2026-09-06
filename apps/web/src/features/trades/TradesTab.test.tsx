import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { TradesTab } from '@features/trades/TradesTab';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('TradesTab', () => {
  it('renders the table header', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Время')).toBeInTheDocument();
      expect(screen.getByText('Сторона')).toBeInTheDocument();
      expect(screen.getByText('Стратегия')).toBeInTheDocument();
    });
  });

  it('renders all 12 trades from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      const buyBadges = screen.getAllByText('BUY');
      const sellBadges = screen.getAllByText('SELL');
      expect(buyBadges.length + sellBadges.length).toBe(12);
    });
  });

  it('renders the strategy name from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('XGB v2.3').length).toBeGreaterThan(0);
    });
  });

  it('renders P&L column with both positive and negative values', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <TradesTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.text-green').length).toBeGreaterThan(0);
      expect(container.querySelectorAll('.text-red').length).toBeGreaterThan(0);
    });
  });
});
