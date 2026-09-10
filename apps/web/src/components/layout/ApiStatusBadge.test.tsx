import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiStatusBadge } from '@components/layout/ApiStatusBadge';

describe('ApiStatusBadge', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders in mock mode when fetch fails (no backend)', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.reject(new Error('offline')),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    const badge = await screen.findByTestId('api-status-badge');
    expect(badge.getAttribute('data-mode')).toBe('mock');
  });

  it('renders in live mode when /health returns 200', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('ok', { status: 200 })),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    const badge = await screen.findByTestId('api-status-badge');
    expect(badge.getAttribute('data-mode')).toBe('live');
  });

  it('persists the last observed mode in localStorage', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('ok', { status: 200 })),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    await waitFor(() =>
      expect(
        screen.getByTestId('api-status-badge').getAttribute('data-mode'),
      ).toBe('live'),
    );
    expect(localStorage.getItem('algotrader.apiMode')).toBe('live');
  });

  it('handles localStorage throwing on read', async () => {
    const spy = vi
      .spyOn(Storage.prototype, 'getItem')
      .mockImplementation(() => {
        throw new Error('quota exceeded');
      });
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('ok', { status: 200 })),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    await waitFor(() =>
      expect(
        screen.getByTestId('api-status-badge').getAttribute('data-mode'),
      ).toBe('live'),
    );
    spy.mockRestore();
  });

  it('handles localStorage throwing on write', async () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota exceeded');
      });
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('ok', { status: 200 })),
    ) as typeof fetch;
    expect(() => render(<ApiStatusBadge />)).not.toThrow();
    await waitFor(() =>
      expect(
        screen.getByTestId('api-status-badge').getAttribute('data-mode'),
      ).toBe('live'),
    );
    spy.mockRestore();
  });

  it('handles AbortController timeout when backend is slow', async () => {
    // Never resolves before abort fires — simulates a hung backend.
    globalThis.fetch = vi.fn(
      (_url: RequestInfo | URL, init?: RequestInit) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    const badge = await screen.findByTestId('api-status-badge');
    expect(badge.getAttribute('data-mode')).toBe('mock');
  });

  it('treats non-2xx /health responses as mock', async () => {
    globalThis.fetch = vi.fn(() =>
      Promise.resolve(new Response('down', { status: 503 })),
    ) as typeof fetch;
    render(<ApiStatusBadge />);
    const badge = await screen.findByTestId('api-status-badge');
    expect(badge.getAttribute('data-mode')).toBe('mock');
  });

});
