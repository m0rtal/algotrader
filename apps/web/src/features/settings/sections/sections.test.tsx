import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { BrokerSection } from '@features/settings/sections/BrokerSection';
import { RiskSection } from '@features/settings/sections/RiskSection';
import { MLSection } from '@features/settings/sections/MLSection';
import { DataSection } from '@features/settings/sections/DataSection';
import type { BrokerSettings, RiskSettings, MLSettings, DataSettings } from '@algotrader/shared';

// Stub useSaveToken — real hook needs QueryClientProvider which BrokerSection
// tests don't currently wrap. Components render OK without mutation; tests
// that exercise the mutation directly use vi.spyOn on this stub.
vi.mock('@lib/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@lib/hooks')>();
  return {
    ...actual,
    useSaveToken: () => ({
      mutateAsync: vi.fn().mockResolvedValue({ tokenLast4: 'xxxx', tokenRedacted: true }),
      isPending: false,
      reset: vi.fn(),
    }),
  };
});

const broker: BrokerSettings = {
  environment: 'sandbox',
  tokenLast4: 'ABCD',
  tokenRedacted: true,
  accountId: 'ACC-1',
};

const risk: RiskSettings = {
  maxDrawdownPct: 10,
  maxPositionSizePct: 20,
  killSwitchEnabled: false,
  killSwitchThresholdPct: 15,
};

const ml: MLSettings = {
  modelVersion: 'v2.3',
  retrainIntervalDays: 30,
  confidenceThreshold: 0.6,
  regimeFilter: 'all',
};

const data: DataSettings = {
  source: 'tinkoff',
  cacheTtlMinutes: 60,
  historyYears: 5,
  autoFetch: true,
};

describe('BrokerSection', () => {
  it('renders environment, token (editable), account id', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Sandbox (безопасно)')).toBeInTheDocument();
    // Token is now an editable input — placeholder guides paste.
    expect(screen.getByPlaceholderText(/Вставьте токен Tinkoff/)).toBeInTheDocument();
    expect(screen.getByDisplayValue('ACC-1')).toBeInTheDocument();
  });

  it('selecting live shows confirm dialog', async () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Окружение' });
    fireEvent.change(select, { target: { value: 'live' } });
    await waitFor(() => {
      expect(screen.getByText('Включить live-торговлю?')).toBeInTheDocument();
    });
  });

  it('canceling live confirm reverts to sandbox', async () => {
    let current: BrokerSettings = broker;
    const handleChange = vi.fn((patch: Partial<BrokerSettings>) => {
      current = { ...current, ...patch };
    });
    const { rerender } = render(
      <BrokerSection
        values={broker}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Окружение' });
    fireEvent.change(select, { target: { value: 'live' } });
    await waitFor(() => {
      expect(screen.getByText('Включить live-торговлю?')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(handleChange).not.toHaveBeenCalledWith(expect.objectContaining({ environment: 'live' }));
    // Dialog closes
    await waitFor(() => {
      expect(screen.queryByText('Включить live-торговлю?')).not.toBeInTheDocument();
    });
    rerender(
      <BrokerSection
        values={current}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
  });

  it('confirming live confirm fires onChange with live', async () => {
    const handleChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByRole('combobox', { name: 'Окружение' }), {
      target: { value: 'live' },
    });
    await waitFor(() => {
      expect(screen.getByText('Включить live-торговлю?')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Включить Live' }));
    expect(handleChange).toHaveBeenCalledWith({ environment: 'live' });
  });

  it('account id input fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByDisplayValue('ACC-1');
    fireEvent.change(input, { target: { value: 'NEW' } });
    expect(handleChange).toHaveBeenCalledWith({ accountId: 'NEW' });
  });

  it('selecting sandbox when already sandbox fires onChange without confirm', () => {
    const onChange = vi.fn();
    const sandbox: BrokerSettings = { ...broker, environment: 'sandbox' };
    render(
      <BrokerSection
        values={sandbox}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Окружение' });
    fireEvent.change(select, { target: { value: 'sandbox' } });
    expect(onChange).toHaveBeenCalledWith({ environment: 'sandbox' });
  });

  it('confirming live trading fires onChange with environment=live', async () => {
    const onChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Окружение' });
    fireEvent.change(select, { target: { value: 'live' } });
    await waitFor(() => expect(screen.getByText('Включить live-торговлю?')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Включить Live' }));
    expect(onChange).toHaveBeenCalledWith({ environment: 'live' });
  });

  it('cancelling live trading confirm keeps environment as sandbox', async () => {
    const onChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Окружение' });
    fireEvent.change(select, { target: { value: 'live' } });
    await waitFor(() => expect(screen.getByText('Включить live-торговлю?')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('account id input fires onChange', () => {
    const onChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Account ID');
    fireEvent.change(input, { target: { value: 'NEW-ID' } });
    expect(onChange).toHaveBeenCalledWith({ accountId: 'NEW-ID' });
  });

  it('renders the Save button', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={vi.fn()}
        saving={false}
        dirty={true}
      />,
    );
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
  });

  it('disables Save when not dirty', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={vi.fn()}
        saving={false}
        dirty={false}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Сохранить' });
    expect(btn).toBeDisabled();
  });

  it('shows saving state', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={vi.fn()}
        saving={true}
        dirty={true}
      />,
    );
    expect(screen.getByText('Сохранение…')).toBeInTheDocument();
  });

  it('renders Save token button disabled when draft is empty', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const btn = screen.getByTestId('save-token') as HTMLButtonElement;
    expect(btn).toBeInTheDocument();
    expect(btn.disabled).toBe(true);
  });

  it('enables Save token when draft has value', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 'new.token' },
    });
    expect(screen.getByTestId('save-token')).not.toBeDisabled();
  });

  it('typing into token field marks tokenRedacted via onChange', () => {
    const handleChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 'abc' },
    });
    expect(handleChange).toHaveBeenCalledWith({ tokenRedacted: true });
  });

  it('shows last-4 hint when tokenLast4 is set and field untouched', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Текущий: ••••••••ABCD/)).toBeInTheDocument();
  });

  it('shows "no token" hint when tokenLast4 is empty', () => {
    render(
      <BrokerSection
        values={{ ...broker, tokenLast4: '' }}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Токен не задан/)).toBeInTheDocument();
  });

  it('hides last-4 hint after user edits draft', () => {
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 'x' },
    });
    expect(screen.queryByText(/Текущий: ••••••••ABCD/)).not.toBeInTheDocument();
    expect(screen.getByText(/Нажмите «Сохранить токен»/)).toBeInTheDocument();
  });

  it('clicking Save token calls useSaveToken mutation with draft', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ tokenLast4: '9999', tokenRedacted: true });
    const useSaveTokenSpy = vi.spyOn(await import('@lib/hooks'), 'useSaveToken').mockReturnValue({
      mutateAsync,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof import('@lib/hooks').useSaveToken>);
    const handleChange = vi.fn();
    render(
      <BrokerSection
        values={broker}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 't.realval.9999' },
    });
    fireEvent.click(screen.getByTestId('save-token'));
    await waitFor(() => {
      expect(mutateAsync).toHaveBeenCalledWith({ token: 't.realval.9999' });
    });
    expect(handleChange).toHaveBeenCalledWith({ tokenLast4: '9999', tokenRedacted: true });
    useSaveTokenSpy.mockRestore();
  });

  it('shows error message on Save token failure', async () => {
    const mutateAsync = vi.fn().mockRejectedValue(new Error('network down'));
    vi.spyOn(await import('@lib/hooks'), 'useSaveToken').mockReturnValue({
      mutateAsync,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof import('@lib/hooks').useSaveToken>);
    render(
      <BrokerSection
        values={broker}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 't.realval' },
    });
    fireEvent.click(screen.getByTestId('save-token'));
    await waitFor(() => {
      expect(screen.getByText(/Ошибка: network down/)).toBeInTheDocument();
    });
  });

  it('shows em-dash fallback in saved hint when tokenLast4 stays empty', async () => {
    const mutateAsync = vi.fn().mockResolvedValue({ tokenLast4: '', tokenRedacted: true });
    vi.spyOn(await import('@lib/hooks'), 'useSaveToken').mockReturnValue({
      mutateAsync,
      isPending: false,
      reset: vi.fn(),
    } as unknown as ReturnType<typeof import('@lib/hooks').useSaveToken>);
    render(
      <BrokerSection
        values={{ ...broker, tokenLast4: '' }}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 't.realval' },
    });
    fireEvent.click(screen.getByTestId('save-token'));
    await waitFor(() => {
      expect(screen.getByText(/последние 4: —/)).toBeInTheDocument();
    });
  });
});

describe('RiskSection', () => {
  it('renders all 4 fields with current values', () => {
    render(
      <RiskSection
        values={risk}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const dd = screen.getByLabelText('Макс drawdown (%)') as HTMLInputElement;
    expect(dd.value).toBe('10');
  });

  it('changing drawdown fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <RiskSection
        values={risk}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Макс drawdown (%)');
    fireEvent.change(input, { target: { value: '25' } });
    expect(handleChange).toHaveBeenCalledWith({ maxDrawdownPct: 25 });
  });

  it('shows validation error for out-of-range drawdown', () => {
    const bad: RiskSettings = { ...risk, maxDrawdownPct: 100 };
    render(
      <RiskSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Максимум 50%')).toBeInTheDocument();
  });

  it('shows validation error for drawdown below minimum', () => {
    const bad: RiskSettings = { ...risk, maxDrawdownPct: 0 };
    render(
      <RiskSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Минимум 1%')).toBeInTheDocument();
  });

  it('shows error when killSwitchThreshold < maxDrawdown', () => {
    const bad: RiskSettings = {
      ...risk,
      maxDrawdownPct: 20,
      killSwitchEnabled: true,
      killSwitchThresholdPct: 10,
    };
    render(
      <RiskSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(
      screen.getByText(/Kill switch порог должен быть больше max drawdown/),
    ).toBeInTheDocument();
  });

  it('toggling kill switch shows confirm when disabling', async () => {
    const enabled: RiskSettings = { ...risk, killSwitchEnabled: true };
    render(
      <RiskSection
        values={enabled}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const checkbox = screen.getByRole('checkbox', { name: 'Kill switch' });
    fireEvent.click(checkbox);
    await waitFor(() => {
      expect(screen.getByText('Отключить kill switch?')).toBeInTheDocument();
    });
  });

  it('toggling kill switch on does not show confirm', () => {
    const handleChange = vi.fn();
    render(
      <RiskSection
        values={risk}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const checkbox = screen.getByRole('checkbox', { name: 'Kill switch' });
    fireEvent.click(checkbox);
    expect(handleChange).toHaveBeenCalledWith({ killSwitchEnabled: true });
  });

  it('disables kill switch threshold when kill switch is off', () => {
    render(
      <RiskSection
        values={risk}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Kill switch порог (%)') as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it('enables kill switch threshold when kill switch is on', () => {
    const enabled: RiskSettings = { ...risk, killSwitchEnabled: true };
    render(
      <RiskSection
        values={enabled}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Kill switch порог (%)') as HTMLInputElement;
    expect(input.disabled).toBe(false);
  });

  it('confirming kill switch disable fires onChange false', async () => {
    const onChange = vi.fn();
    const enabled: RiskSettings = { ...risk, killSwitchEnabled: true };
    render(
      <RiskSection
        values={enabled}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: 'Kill switch' }));
    await waitFor(() => expect(screen.getByText('Отключить kill switch?')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Отключить' }));
    expect(onChange).toHaveBeenCalledWith({ killSwitchEnabled: false });
  });

  it('clicking threshold input fires onChange', () => {
    const onChange = vi.fn();
    const enabled: RiskSettings = { ...risk, killSwitchEnabled: true };
    render(
      <RiskSection
        values={enabled}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByLabelText('Kill switch порог (%)'), { target: { value: '20' } });
    expect(onChange).toHaveBeenCalledWith({ killSwitchThresholdPct: 20 });
  });

  it('changing max position size fires onChange', () => {
    const onChange = vi.fn();
    render(
      <RiskSection
        values={risk}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByLabelText('Макс размер позиции (%)'), { target: { value: '30' } });
    expect(onChange).toHaveBeenCalledWith({ maxPositionSizePct: 30 });
  });

  it('changing max drawdown fires onChange', () => {
    const onChange = vi.fn();
    render(
      <RiskSection
        values={risk}
        onChange={onChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    fireEvent.change(screen.getByLabelText('Макс drawdown (%)'), { target: { value: '15' } });
    expect(onChange).toHaveBeenCalledWith({ maxDrawdownPct: 15 });
  });

  it('threshold input is disabled when section disabled', () => {
    const enabled: RiskSettings = { ...risk, killSwitchEnabled: true };
    render(
      <RiskSection
        values={enabled}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
        disabled
      />,
    );
    expect(screen.getByLabelText('Kill switch порог (%)')).toBeDisabled();
  });

  it('kill switch checkbox disabled when section disabled', () => {
    render(
      <RiskSection
        values={risk}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
        disabled
      />,
    );
    expect(screen.getByLabelText('Kill switch')).toBeDisabled();
  });
});

describe('MLSection', () => {
  it('renders model version as readonly', () => {
    render(
      <MLSection values={ml} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    const input = screen.getByDisplayValue('v2.3') as HTMLInputElement;
    expect(input.readOnly).toBe(true);
  });

  it('changing retrain interval fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <MLSection
        values={ml}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Интервал retrain (дни)');
    fireEvent.change(input, { target: { value: '45' } });
    expect(handleChange).toHaveBeenCalledWith({ retrainIntervalDays: 45 });
  });

  it('changing confidence fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <MLSection
        values={ml}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Confidence threshold');
    fireEvent.change(input, { target: { value: '0.8' } });
    expect(handleChange).toHaveBeenCalledWith({ confidenceThreshold: 0.8 });
  });

  it('shows validation for retrain interval out of range', () => {
    const bad: MLSettings = { ...ml, retrainIntervalDays: 200 };
    render(
      <MLSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    expect(screen.getByText('Максимум 90 дней')).toBeInTheDocument();
  });

  it('shows validation for confidence > 1', () => {
    const bad: MLSettings = { ...ml, confidenceThreshold: 1.5 };
    render(
      <MLSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    expect(screen.getByText('Максимум 1')).toBeInTheDocument();
  });

  it('regime filter has all 4 options', () => {
    render(
      <MLSection values={ml} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    const select = screen.getByRole('combobox', { name: 'Regime filter' });
    expect(select.querySelectorAll('option')).toHaveLength(4);
  });

  it('changing regime filter fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <MLSection
        values={ml}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Regime filter' });
    fireEvent.change(select, { target: { value: 'trend' } });
    expect(handleChange).toHaveBeenCalledWith({ regimeFilter: 'trend' });
  });

  it('shows error for retrain interval < 1', () => {
    const bad: MLSettings = { ...ml, retrainIntervalDays: 0 };
    render(
      <MLSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    expect(screen.getByText('Минимум 1 день')).toBeInTheDocument();
  });

  it('shows error for confidence < 0', () => {
    const bad: MLSettings = { ...ml, confidenceThreshold: -0.2 };
    render(
      <MLSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty={false} />,
    );
    expect(screen.getByText('Минимум 0')).toBeInTheDocument();
  });

  it('disables save when validation fails', () => {
    const bad: MLSettings = { ...ml, retrainIntervalDays: 0 };
    render(<MLSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty />);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('disables fields when disabled prop set', () => {
    render(
      <MLSection
        values={ml}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
        disabled
      />,
    );
    expect(screen.getByLabelText('Интервал retrain (дни)')).toBeDisabled();
  });
});

describe('DataSection', () => {
  it('renders all 4 fields', () => {
    render(
      <DataSection
        values={data}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Tinkoff API')).toBeInTheDocument();
    expect(screen.getByDisplayValue('60')).toBeInTheDocument();
    expect(screen.getByDisplayValue('5')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Auto-fetch' })).toBeInTheDocument();
  });

  it('source select has 3 options', () => {
    render(
      <DataSection
        values={data}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Источник' });
    expect(select.querySelectorAll('option')).toHaveLength(3);
  });

  it('changing cache TTL fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <DataSection
        values={data}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('Cache TTL (мин)');
    fireEvent.change(input, { target: { value: '120' } });
    expect(handleChange).toHaveBeenCalledWith({ cacheTtlMinutes: 120 });
  });

  it('changing history years fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <DataSection
        values={data}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const input = screen.getByLabelText('История (лет)');
    fireEvent.change(input, { target: { value: '7' } });
    expect(handleChange).toHaveBeenCalledWith({ historyYears: 7 });
  });

  it('toggling auto-fetch fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <DataSection
        values={data}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const cb = screen.getByRole('checkbox', { name: 'Auto-fetch' });
    fireEvent.click(cb);
    expect(handleChange).toHaveBeenCalledWith({ autoFetch: false });
  });

  it('shows validation for cache TTL > 1440', () => {
    const bad: DataSettings = { ...data, cacheTtlMinutes: 9999 };
    render(
      <DataSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Максимум 1440/)).toBeInTheDocument();
  });

  it('shows validation for history years > 10', () => {
    const bad: DataSettings = { ...data, historyYears: 99 };
    render(
      <DataSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Максимум 10 лет')).toBeInTheDocument();
  });

  it('shows validation for cache TTL < 1', () => {
    const bad: DataSettings = { ...data, cacheTtlMinutes: 0 };
    render(
      <DataSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Минимум 1 минута')).toBeInTheDocument();
  });

  it('shows validation for history years < 1', () => {
    const bad: DataSettings = { ...data, historyYears: 0 };
    render(
      <DataSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText('Минимум 1 год')).toBeInTheDocument();
  });

  it('disables save when validation fails', () => {
    const bad: DataSettings = { ...data, cacheTtlMinutes: 0 };
    render(<DataSection values={bad} onChange={() => {}} onSave={() => {}} saving={false} dirty />);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('disables fields when disabled prop set', () => {
    render(
      <DataSection
        values={data}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
        disabled
      />,
    );
    expect(screen.getByRole('checkbox', { name: 'Auto-fetch' })).toBeDisabled();
  });

  it('changing source fires onChange', () => {
    const handleChange = vi.fn();
    render(
      <DataSection
        values={data}
        onChange={handleChange}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    const select = screen.getByRole('combobox', { name: 'Источник' });
    fireEvent.change(select, { target: { value: 'moex_iss' } });
    expect(handleChange).toHaveBeenCalledWith({ source: 'moex_iss' });
  });
});
