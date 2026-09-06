import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { LogStrip } from '@components/layout/LogStrip';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('LogStrip', () => {
  it('renders log entries from mock data', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText(/fetch\.py/)).toBeInTheDocument();
      expect(screen.getByText(/features\.py/)).toBeInTheDocument();
    });
  });

  it('renders the warn-tone class on a warn log entry', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelector('.text-amber')).toBeTruthy();
    });
  });

  it('renders the ok-tone class on ok log entries', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <LogStrip />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.text-green').length).toBeGreaterThan(0);
    });
  });
});
