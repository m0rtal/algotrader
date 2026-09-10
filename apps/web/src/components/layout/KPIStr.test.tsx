import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { KPIStr } from '@components/layout/KPIStr';
import { server } from '../../mocks/server';

// Zod-compliant Kpi fixtures: { label, value, sub?, tone: 'pos'|'neg'|'flat'|'neutral' }.
// 6 entries to match the layout grid (2/3/6 cols).
const sampleKpis = [
  { label: 'Equity', value: '1 124 380', sub: '+2.41%', tone: 'pos' as const },
  { label: 'P&L день', value: '+18 240', sub: '+1.65%', tone: 'pos' as const },
  { label: 'P&L месяц', value: '+94 510', sub: '+9.18%', tone: 'pos' as const },
  { label: 'Sharpe (90д)', value: '1.84', sub: 'стабильно', tone: 'pos' as const },
  { label: 'Max DD', value: '-8.32%', sub: '-2.10%', tone: 'neg' as const },
  { label: 'Позиций', value: '12', sub: '8 лонг / 4 шорт', tone: 'neutral' as const },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('KPIStr', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/kpis', () => HttpResponse.json(sampleKpis)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

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
