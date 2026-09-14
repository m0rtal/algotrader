import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import '@testing-library/jest-dom/vitest';
import { http, HttpResponse } from 'msw';

import { DataQualityTab } from '@features/dataQuality/DataQualityTab';
import { server } from '../../mocks/server';

/**
 * Tests for issue #2 — the missing data-quality UI tab.
 *
 * Coverage:
 * - DataQualityTab renders the HealthReport fields (health_score, issues)
 * - Empty state shown on 404
 * - Error state shown on 5xx with a refetch affordance
 *
 * Note: MSW handlers in this codebase are pure passthroughs; tests
 * install canned data via server.use() per the project convention
 * (see BackfillTab.test.tsx for the pattern).
 */

const defaultHealth = {
  figi: 'BBG004730N88',
  ticker: 'SBER',
  health_score: 85,
  issues: ['missing-recent-days'],
  first_bar: '2024-01-15',
  last_bar: '2026-09-12',
  actual_bars: 642,
  expected_bars: 660,
  recent_gaps: ['2026-08-30', '2026-09-01'],
  recent_failures: [],
};

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  server.use(
    http.get('/api/data-quality/SBER', () =>
      HttpResponse.json(defaultHealth)
    ),
  );
});

afterEach(() => {
  server.resetHandlers();
});

describe('DataQualityTab', () => {
  it('renders the health score from the API response', async () => {
    render(<DataQualityTab ticker="SBER" />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(screen.getByTestId('data-quality-score')).toBeInTheDocument();
    });
    expect(screen.getByTestId('data-quality-score')).toHaveTextContent('85');
  });

  it('renders the list of issues from the API response', async () => {
    render(<DataQualityTab ticker="SBER" />, { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(
        screen.getByTestId('data-quality-issues')
      ).toBeInTheDocument();
    });
    // The backend returns kebab-case strings (e.g. "missing-recent-days").
    expect(screen.getByTestId('data-quality-issues').textContent)
      .toContain('missing-recent-days');
  });

  it('renders a placeholder when the backend returns no data', async () => {
    server.use(
      http.get('/api/data-quality/UNKNOWN', () =>
        HttpResponse.json(
          { detail: { error: 'unknown_symbol' } },
          { status: 404 }
        )
      ),
    );
    render(<DataQualityTab ticker="UNKNOWN" />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(
        screen.getByTestId('data-quality-empty')
      ).toBeInTheDocument();
    });
  });

  it('shows a refetch button when the API call fails', async () => {
    server.use(
      http.get('/api/data-quality/SBER', () =>
        HttpResponse.json({}, { status: 500 })
      ),
    );
    render(<DataQualityTab ticker="SBER" />, { wrapper: makeWrapper() });
    await waitFor(() => {
      expect(screen.getByTestId('data-quality-error')).toBeInTheDocument();
    });
    expect(screen.getByTestId('data-quality-refetch')).toBeInTheDocument();
  });
});
