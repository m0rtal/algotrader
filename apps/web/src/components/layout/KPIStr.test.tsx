import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { KPIStr } from '@components/layout/KPIStr';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('KPIStr', () => {
  it('renders 6 KPI cells with mocked data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <KPIStr />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Equity')).toBeInTheDocument();
      expect(screen.getByText('P&L день')).toBeInTheDocument();
      expect(screen.getByText('P&L месяц')).toBeInTheDocument();
      expect(screen.getByText('Sharpe (90д)')).toBeInTheDocument();
      expect(screen.getByText('Max DD')).toBeInTheDocument();
      expect(screen.getByText('Позиций')).toBeInTheDocument();
    });
  });

  it('renders the equity value from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <KPIStr />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/1 124 380/)).toBeInTheDocument();
    });
  });

  it('renders Sharpe value from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <KPIStr />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('1.84')).toBeInTheDocument();
    });
  });

  it('applies positive tone to positive KPIs', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <KPIStr />
      </Wrapper>,
    );
    await waitFor(() => {
      const greenElements = container.querySelectorAll('.text-green');
      expect(greenElements.length).toBeGreaterThan(0);
    });
  });

  it('applies negative tone to Max DD', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <KPIStr />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelector('.text-red')).toBeTruthy();
    });
  });
});
