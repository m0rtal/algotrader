import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { Topbar } from '@components/layout/Topbar';

describe('Topbar', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders the brand and a Sandbox label', () => {
    render(<Topbar />);
    expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
  });

  it('shows the API status badge in mock mode when /health is unreachable', async () => {
    // Default MSW handler doesn't proxy /health, so fetch fails → mock.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() =>
      Promise.reject(new Error('network unreachable')),
    ) as typeof fetch;
    try {
      render(<Topbar />);
      await waitFor(() => {
        const badge = screen.getByTestId('api-status-badge');
        expect(badge.getAttribute('data-mode')).toBe('mock');
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it('shows the API status badge in live mode when /health returns 200', async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('ok', { status: 200 })),
    ) as typeof fetch;
    try {
      render(<Topbar />);
      await waitFor(() => {
        const badge = screen.getByTestId('api-status-badge');
        expect(badge.getAttribute('data-mode')).toBe('live');
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
