import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PortfolioTab } from '@features/portfolio/PortfolioTab';
import { server } from '../../mocks/server';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const samplePortfolio = {
  cash: 100000,
  invested: 1124380,
  total: 1224380,
  longCount: 2,
  shortCount: 1,
  grossExposure: 1.05,
  netExposure: 0.95,
  positions: [
    { symbol: 'SBER', side: 'long', qty: 10, avgPrice: 300, price: 312.4, value: 3124, weight: 0.0026, pnl: 124 },
    { symbol: 'GAZP', side: 'long', qty: 20, avgPrice: 120, price: 128.65, value: 2573, weight: 0.0021, pnl: 173 },
    { symbol: 'YNDX', side: 'short', qty: 5, avgPrice: 4500, price: 4218, value: 21090, weight: 0.0172, pnl: 1410 },
  ],
};

describe('PortfolioTab', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/portfolio', () => HttpResponse.json(samplePortfolio)),
    );
  });
  afterEach(() => server.resetHandlers());

  it('renders the summary cards', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PortfolioTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Свободно')).toBeInTheDocument();
      expect(screen.getByText('В позициях')).toBeInTheDocument();
      expect(screen.getByText('Позиций')).toBeInTheDocument();
      expect(screen.getByText('Gross exposure')).toBeInTheDocument();
    });
  });

  it('renders the positions table', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PortfolioTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('SBER')).toBeInTheDocument();
      expect(screen.getByText('GAZP')).toBeInTheDocument();
      expect(screen.getByText('Сторона')).toBeInTheDocument();
      expect(screen.getByText('Средняя')).toBeInTheDocument();
      expect(screen.getByText('Доля')).toBeInTheDocument();
    });
  });

  it('displays LONG and SHORT badges', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <PortfolioTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('LONG').length).toBeGreaterThan(0);
      expect(screen.getAllByText('SHORT').length).toBeGreaterThan(0);
    });
  });

  it('displays weight bars (bg-accent divs)', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <PortfolioTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.bg-accent').length).toBeGreaterThan(0);
    });
  });
});
