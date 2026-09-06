import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { PortfolioTab } from '@features/portfolio/PortfolioTab';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('PortfolioTab', () => {
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
      // weight bars use .bg-accent
      expect(container.querySelectorAll('.bg-accent').length).toBeGreaterThan(0);
    });
  });
});
