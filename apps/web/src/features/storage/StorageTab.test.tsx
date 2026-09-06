import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { StorageTab } from '@features/storage/StorageTab';
import { useUiStore } from '@stores/uiStore';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  Wrapper.displayName = 'TestWrapper';
  return Wrapper;
}

describe('StorageTab', () => {
  it('renders the 4 storage summary cards', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <StorageTab />
      </Wrapper>,
    );
    expect(await screen.findByText('Всего баров', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByText('Размер на диске')).toBeInTheDocument();
    expect(screen.getByText('Полнота данных')).toBeInTheDocument();
  });

  it('renders the per-ticker table', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <StorageTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('SBER').length).toBeGreaterThan(0);
      expect(screen.getAllByText('GAZP').length).toBeGreaterThan(0);
      expect(screen.getAllByText('YNDX').length).toBeGreaterThan(0);
    });
  });

  it('shows bar counts and last update', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <StorageTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText(/1 247/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/2026-09-06 19:31/).length).toBeGreaterThan(0);
    });
  });

  it('clicking a ticker row sets selectedTicker in store', async () => {
    useUiStore.setState({ selectedTicker: null });
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <StorageTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getAllByText('YNDX').length).toBeGreaterThan(0);
    });
    const rows = screen.getAllByText('YNDX');
    // first is in the table body (button rows from sidebar not rendered here)
    fireEvent.click(rows[0]!);
    expect(useUiStore.getState().selectedTicker).toBe('YNDX');
  });

  it('highlights rows with gaps in amber', async () => {
    const Wrapper = makeWrapper();
    const { container } = render(
      <Wrapper>
        <StorageTab />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(container.querySelectorAll('.text-amber').length).toBeGreaterThan(0);
    });
  });
});
