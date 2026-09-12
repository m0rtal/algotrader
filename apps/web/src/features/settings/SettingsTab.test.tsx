import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DEFAULT_SETTINGS } from '@algotrader/shared';
import { SettingsTab } from '@features/settings/SettingsTab';
import { useSettingsStore } from '@stores/settingsStore';
import { server } from '../../mocks/server';

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

const stored = {
  values: DEFAULT_SETTINGS,
  version: 'v1-test',
};

describe('SettingsTab', () => {
  beforeEach(() => {
    useSettingsStore.setState({
      values: DEFAULT_SETTINGS,
      baseline: DEFAULT_SETTINGS,
      version: null,
      saving: false,
      error: null,
      loaded: false,
    });
    // Default happy-path MSW handlers for GET/PUT/DELETE on
    // /api/settings. Individual tests override with server.use() to
    // simulate conflict / 500 / etc.
    server.use(
      http.get('/api/settings', () =>
        HttpResponse.json({ ...stored, updatedAt: new Date().toISOString() }),
      ),
      http.put('/api/settings', async ({ request }) => {
        const body = (await request.json()) as { values: typeof DEFAULT_SETTINGS };
        return HttpResponse.json({
          values: body.values,
          version: 'v1-new',
          updatedAt: new Date().toISOString(),
        });
      }),
      http.delete('/api/settings', () => new HttpResponse(null, { status: 204 })),
      http.put('/api/settings/token', async ({ request }) => {
        const { token } = (await request.json()) as { token: string };
        return HttpResponse.json({
          tokenLast4: token.slice(-4),
          tokenRedacted: true,
        });
      }),
    );
  });
  afterEach(() => {
    server.resetHandlers();
    useSettingsStore.setState({
      values: DEFAULT_SETTINGS,
      baseline: DEFAULT_SETTINGS,
      version: null,
      saving: false,
      error: null,
      loaded: false,
    });
  });

  it('renders the 4 section tabs', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Брокер' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Риск' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'ML' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Данные' })).toBeInTheDocument();
    });
  });

  it('shows Broker section by default with all fields', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => {
      expect(screen.getAllByText('Брокер').length).toBeGreaterThan(0);
      expect(screen.getByText('Окружение')).toBeInTheDocument();
      expect(screen.getByText('Токен')).toBeInTheDocument();
      expect(screen.getByText('Account ID')).toBeInTheDocument();
    });
  });

  it('switching to Risk shows risk fields', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Риск' }));
    screen.getByRole('button', { name: 'Риск' }).click();
    await waitFor(() => {
      expect(screen.getByText('Макс drawdown (%)')).toBeInTheDocument();
      expect(screen.getByText('Макс размер позиции (%)')).toBeInTheDocument();
      expect(screen.getByText('Kill switch')).toBeInTheDocument();
    });
  });

  it('clicking save sends version v1 when store version is null', async () => {
    let receivedVersion: string | undefined;
    server.use(
      http.put('/api/settings', async ({ request }) => {
        const body = (await request.json()) as { version: string };
        receivedVersion = body.version;
        return HttpResponse.json({
          values: DEFAULT_SETTINGS,
          version: 'v1-new',
          updatedAt: new Date().toISOString(),
        });
      }),
    );
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сохранить' }));
    act(() => useSettingsStore.setState({ version: null }));
    fireEvent.change(screen.getByLabelText('Account ID'), { target: { value: 'Y' } });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(screen.getByText(/Сохранено · sandbox/)).toBeInTheDocument());
    expect(receivedVersion).toBe('v1');
  });

  it('switching to ML shows ml fields with readonly model version', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'ML' }));
    screen.getByRole('button', { name: 'ML' }).click();
    await waitFor(() => {
      expect(screen.getByText('Версия модели')).toBeInTheDocument();
      expect(screen.getByText('Интервал retrain (дни)')).toBeInTheDocument();
      expect(screen.getByText('Confidence threshold')).toBeInTheDocument();
    });
  });

  it('switching to Data shows data fields', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Данные' }));
    screen.getByRole('button', { name: 'Данные' }).click();
    await waitFor(() => {
      expect(screen.getByText('Источник')).toBeInTheDocument();
      expect(screen.getByText('Cache TTL (мин)')).toBeInTheDocument();
      expect(screen.getByText('История (лет)')).toBeInTheDocument();
      expect(screen.getByText('Auto-fetch')).toBeInTheDocument();
    });
  });

  it('renders the Danger zone', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => {
      expect(screen.getByText('Опасная зона')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Сбросить настройки' })).toBeInTheDocument();
    });
  });

  it('clicking the save button triggers save mutation', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сохранить' }));
    fireEvent.change(screen.getByLabelText('Account ID'), { target: { value: 'NEW' } });
    await waitFor(() => {
      const saveBtn = screen.getByRole('button', { name: 'Сохранить' });
      expect(saveBtn).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => screen.getByText(/Сохранено · sandbox/));
  });

  it('clicking reset shows confirm then triggers reset', async () => {
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сбросить настройки' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить настройки' }));
    await waitFor(() => screen.getByText('Сбросить все настройки к defaults?'));
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }));
    await waitFor(() => screen.getByText('Настройки сброшены'));
  });

  it('handles 409 conflict by showing warning toast', async () => {
    server.use(
      http.put('/api/settings', () =>
        HttpResponse.json(
          {
            error: 'version conflict',
            current: { broker: DEFAULT_SETTINGS.broker, risk: DEFAULT_SETTINGS.risk, ml: DEFAULT_SETTINGS.ml, data: DEFAULT_SETTINGS.data },
          },
          { status: 409 },
        ),
      ),
    );
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сохранить' }));
    fireEvent.change(screen.getByLabelText('Account ID'), { target: { value: 'X' } });
    await waitFor(() => {
      const saveBtn = screen.getByRole('button', { name: 'Сохранить' });
      expect(saveBtn).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() =>
      expect(screen.getByText('Настройки изменены в другом месте. Перезагрузите.')).toBeInTheDocument(),
    );
  });

  it('handles non-409 error by showing error toast', async () => {
    server.use(http.put('/api/settings', () => HttpResponse.json({ error: 'fail' }, { status: 500 })));
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сохранить' }));
    fireEvent.change(screen.getByLabelText('Account ID'), { target: { value: 'X' } });
    await waitFor(() => {
      const saveBtn = screen.getByRole('button', { name: 'Сохранить' });
      expect(saveBtn).not.toBeDisabled();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
    await waitFor(() => expect(screen.getByText('Ошибка сохранения. Попробуйте ещё раз.')).toBeInTheDocument());
  });

  it('shows error toast when reset fails', async () => {
    server.use(http.delete('/api/settings', () => HttpResponse.json({ error: 'fail' }, { status: 500 })));
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => screen.getByRole('button', { name: 'Сбросить настройки' }));
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить настройки' }));
    await waitFor(() => screen.getByText('Сбросить все настройки к defaults?'));
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }));
    await waitFor(() => screen.getByText('Ошибка сброса'));
  });

  it('shows error state on query error', async () => {
    server.use(http.get('/api/settings', () => HttpResponse.json({ error: 'fail' }, { status: 500 })));
    const Wrapper = makeWrapper();
    render(<Wrapper><SettingsTab /></Wrapper>);
    await waitFor(() => expect(screen.getByText(/Ошибка/)).toBeInTheDocument());
  });
});
