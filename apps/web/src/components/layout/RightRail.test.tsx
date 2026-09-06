import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { RightRail } from '@components/layout/RightRail';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('RightRail', () => {
  it('renders the ML Model section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('ML Model')).toBeInTheDocument();
    });
  });

  it('renders the Top features section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Top features')).toBeInTheDocument();
    });
  });

  it('renders the Pipeline status section', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Pipeline status')).toBeInTheDocument();
    });
  });

  it('displays model version', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/v2\.3/)).toBeInTheDocument();
    });
  });

  it('displays OOS Sharpe value', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('1.84')).toBeInTheDocument();
    });
  });

  it('displays all 5 feature importances', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('mom_20d')).toBeInTheDocument();
      expect(screen.getByText('rsi_14')).toBeInTheDocument();
      expect(screen.getByText('pe_zscore')).toBeInTheDocument();
      expect(screen.getByText('sector_rel')).toBeInTheDocument();
      expect(screen.getByText('adv_20d')).toBeInTheDocument();
    });
  });

  it('displays all 7 pipeline steps', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('Universe')).toBeInTheDocument();
      expect(screen.getByText('Fetch')).toBeInTheDocument();
      expect(screen.getByText('Features')).toBeInTheDocument();
      expect(screen.getByText('Regime')).toBeInTheDocument();
      expect(screen.getByText('Model')).toBeInTheDocument();
      expect(screen.getByText('Backtest')).toBeInTheDocument();
      expect(screen.getByText('Broker')).toBeInTheDocument();
    });
  });

  it('displays the Broker step as idle (non-ok branch)', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      // Broker is idle in mock data, so the row value is the detail 'sandbox'
      // The code path: status !== 'ok' && status !== 'idle' would render the status itself
      // For idle, it renders `○ sandbox`
      expect(screen.getByText(/sandbox/)).toBeInTheDocument();
    });
  });

  it('handles warn/err pipeline status', async () => {
    const { server } = await import('../../mocks/server');
    const { http, HttpResponse } = await import('msw');
    server.use(
      http.get('/api/pipeline', () =>
        HttpResponse.json([
          { name: 'Universe', status: 'ok' },
          { name: 'Fetch', status: 'err', detail: 'timeout' },
          { name: 'Features', status: 'warn' },
        ]),
      ),
    );
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    await waitFor(() => {
      // err status renders the status itself (status !== 'ok' && !== 'idle' branch)
      expect(screen.getByText('err')).toBeInTheDocument();
    });
  });
});
