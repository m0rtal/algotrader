import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { RightRail } from '@components/layout/RightRail';
import { server } from '../../mocks/server';

// Test fixtures installed via server.use() — see SignalsTab.test.tsx for
// the established pattern. The dev MSW handlers remain strict passthroughs
// that hit the real backend, so each test opts into its own /api/model,
// /api/model/features, /api/pipeline mocks. Shapes here MUST satisfy the
// Zod schemas in @algotrader/shared (ModelSchema, FeatureImportanceSchema,
// PipelineStepSchema) — TanStack Query parses via .parse() and any drift
// would make the component render nothing.

const sampleModel = {
  version: 'v2.3.1',
  trainWindowMonths: 36,
  trainStart: '2021-09-01T00:00:00Z',
  trainEnd: '2024-09-01T00:00:00Z',
  oosAccuracy: 0.612,
  oosSharpe: 1.84,
  ic: 0.074,
  lastTrainDate: '2026-08-15',
  nextTrainDate: '2026-11-15',
};

const sampleFeatures = [
  { name: 'mom_20d', importance: 0.182 },
  { name: 'rsi_14', importance: 0.154 },
  { name: 'pe_zscore', importance: 0.121 },
  { name: 'sector_rel', importance: 0.098 },
  { name: 'adv_20d', importance: 0.076 },
];

const samplePipeline = [
  { name: 'Universe', status: 'ok' as const, detail: '84 tickers' },
  { name: 'Fetch', status: 'ok' as const, detail: '2026-09-10' },
  { name: 'Features', status: 'ok' as const, detail: '42 cols' },
  { name: 'Regime', status: 'ok' as const, detail: 'trend' },
  { name: 'Model', status: 'ok' as const, detail: 'GBDT' },
  { name: 'Backtest', status: 'ok' as const, detail: 'fold 3/5' },
  { name: 'Broker', status: 'idle' as const, detail: 'sandbox' },
];

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('RightRail', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/model', () => HttpResponse.json(sampleModel)),
      http.get('/api/model/features', () => HttpResponse.json(sampleFeatures)),
      http.get('/api/pipeline', () => HttpResponse.json(samplePipeline)),
    );
  });
  afterEach(() => {
    server.resetHandlers();
  });

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
      expect(screen.getByText('v2.3.1')).toBeInTheDocument();
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
      // Broker is idle in mock data, so the row value is '○ sandbox'.
      expect(screen.getByText(/sandbox/)).toBeInTheDocument();
    });
  });

  it('handles warn/err pipeline status', async () => {
    server.use(
      http.get('/api/pipeline', () =>
        HttpResponse.json([
          { name: 'Universe', status: 'ok', detail: '84 tickers' },
          { name: 'Fetch', status: 'err', detail: 'timeout' },
          { name: 'Features', status: 'warn', detail: 'lag spike' },
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
      // err status falls through to the else branch and renders the
      // status itself (not the detail).
      expect(screen.getByText('err')).toBeInTheDocument();
      expect(screen.getByText('warn')).toBeInTheDocument();
    });
  });

  it('renders nothing while queries are still loading', () => {
    // Force the model query to never resolve so the component's
    // `!model || !features || !pipeline` guard returns null.
    server.use(
      http.get('/api/model', () => new Promise(() => {})),
    );
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <RightRail />
      </Wrapper>,
    );
    expect(container.firstChild).toBeNull();
  });
});
