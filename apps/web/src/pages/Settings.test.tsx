import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Settings } from '@pages/Settings';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('Settings page', () => {
  it('renders the back link and title', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Settings />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByText('НАСТРОЙКИ')).toBeInTheDocument();
      expect(screen.getByText('← Назад')).toBeInTheDocument();
    });
  });

  it('back link points to /', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Settings />
      </Wrapper>,
    );
    const back = screen.getByText('← Назад');
    expect(back.getAttribute('href')).toBe('/');
  });

  it('renders all 4 section tabs', async () => {
    const Wrapper = makeWrapper();
    render(
      <Wrapper>
        <Settings />
      </Wrapper>,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Брокер' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Риск' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'ML' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Данные' })).toBeInTheDocument();
    });
  });
});
