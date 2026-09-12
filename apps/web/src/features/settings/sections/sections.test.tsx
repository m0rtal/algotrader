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
  function renderBroker(
    overrides: Partial<Parameters<typeof BrokerSection>[0]> = {},
  ) {
    const onChange = vi.fn();
    const onSave = vi.fn();
    const props = {
      values: broker,
      onChange,
      onSave,
      saving: false,
      dirty: false,
      tokenDraft: '',
      onTokenDraftChange: vi.fn(),
      ...overrides,
    };
    return { ...render(<BrokerSection {...props} />), onChange, onSave, props };
  }

  it('renders env radio cards, token input with reveal, and account id', () => {
    renderBroker();
    expect(screen.getByRole('radio', { name: /Sandbox/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Live/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Вставьте токен Tinkoff/)).toBeInTheDocument();
    // Reveal toggle renders for the token field.
    expect(screen.getByRole('button', { name: 'Показать токен' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('ACC-1')).toBeInTheDocument();
  });

  it('marks Sandbox radio as checked by default', () => {
    renderBroker();
    expect(screen.getByRole('radio', { name: /Sandbox/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /Live/ })).toHaveAttribute('aria-checked', 'false');
  });

  it('shows inline warn-banner when Live is selected, no confirm dialog', async () => {
    const onChange = vi.fn();
    render(<BrokerSection
      values={{ ...broker, environment: 'live' }}
      onChange={onChange}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByRole('note')).toHaveTextContent(/Live — реальные деньги/);
    // No ConfirmDialog opens.
    expect(screen.queryByText('Включить live-торговлю?')).not.toBeInTheDocument();
  });

  it('hides warn-banner when Sandbox is selected', () => {
    renderBroker();
    expect(screen.queryByRole('note')).not.toBeInTheDocument();
  });

  it('clicking Live radio fires onChange with environment=live', () => {
    const onChange = vi.fn();
    render(<BrokerSection
      values={broker}
      onChange={onChange}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    fireEvent.click(screen.getByRole('radio', { name: /Live/ }));
    expect(onChange).toHaveBeenCalledWith({ environment: 'live' });
  });

  it('clicking Sandbox radio fires onChange with environment=sandbox', () => {
    const onChange = vi.fn();
    render(<BrokerSection
      values={{ ...broker, environment: 'live' }}
      onChange={onChange}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    fireEvent.click(screen.getByRole('radio', { name: /Sandbox/ }));
    expect(onChange).toHaveBeenCalledWith({ environment: 'sandbox' });
  });

  it('account id input fires onChange', () => {
    const onChange = vi.fn();
    render(<BrokerSection
      values={broker}
      onChange={onChange}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    fireEvent.change(screen.getByLabelText('Account ID'), { target: { value: 'NEW-ID' } });
    expect(onChange).toHaveBeenCalledWith({ accountId: 'NEW-ID' });
  });

  it('token reveal toggle changes input type from password to text', () => {
    renderBroker();
    const input = screen.getByPlaceholderText(/Вставьте токен Tinkoff/) as HTMLInputElement;
    expect(input.type).toBe('password');
    fireEvent.click(screen.getByRole('button', { name: 'Показать токен' }));
    expect(input.type).toBe('text');
    expect(screen.getByRole('button', { name: 'Скрыть токен' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('token input forwards value via onTokenDraftChange', () => {
    const onTokenDraftChange = vi.fn();
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={onTokenDraftChange}
    />);
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 't.short' },
    });
    expect(onTokenDraftChange).toHaveBeenCalledWith('t.short');
  });

  it('shows validation error when token does not start with t.', () => {
    const { rerender } = render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    // Touch the field by typing once; that flips `touched` inside the
    // component so validation becomes active.
    fireEvent.change(screen.getByPlaceholderText(/Вставьте токен Tinkoff/), {
      target: { value: 'abcd_long_enough_for_min_check' },
    });
    rerender(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft="abcd_long_enough_for_min_check"
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByRole('alert')).toHaveTextContent(/начинается с «t\.»/);
  });

  it('does not show validation error before user touches the field', () => {
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft="bad"
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows last-4 hint when token is set and draft is empty', () => {
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByText(/Текущий: t\.••••••••ABCD/)).toBeInTheDocument();
  });

  it('shows "token not set" hint when tokenLast4 is empty and draft is empty', () => {
    render(<BrokerSection
      values={{ ...broker, tokenLast4: '' }}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByText(/Токен не задан/)).toBeInTheDocument();
  });

  it('shows empty-account hint when accountId is empty', () => {
    render(<BrokerSection
      values={{ ...broker, accountId: '' }}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByText(/Укажите аккаунт Tinkoff/)).toBeInTheDocument();
  });

  it('Save button is rendered inside the Section', () => {
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={vi.fn()}
      saving={false}
      dirty
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
  });

  it('Save button is disabled when not dirty', () => {
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={vi.fn()}
      saving={false}
      dirty={false}
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('Save button shows saving state', () => {
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={vi.fn()}
      saving
      dirty
      tokenDraft=""
      onTokenDraftChange={vi.fn()}
    />);
    expect(screen.getByText('Сохранение…')).toBeInTheDocument();
  });

  it('clearing the token field via the clear button calls onTokenDraftChange with empty', () => {
    const onTokenDraftChange = vi.fn();
    render(<BrokerSection
      values={broker}
      onChange={() => {}}
      onSave={() => {}}
      saving={false}
      dirty={false}
      tokenDraft="t.already_have_some_value"
      onTokenDraftChange={onTokenDraftChange}
    />);
    fireEvent.click(screen.getByRole('button', { name: 'Очистить' }));
    expect(onTokenDraftChange).toHaveBeenCalledWith('');
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

  it('shows validation error for position size below minimum', () => {
    const bad: RiskSettings = { ...risk, maxPositionSizePct: 0 };
    render(
      <RiskSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Минимум 1%/)).toBeInTheDocument();
  });

  it('shows validation error for position size above maximum', () => {
    const bad: RiskSettings = { ...risk, maxPositionSizePct: 999 };
    render(
      <RiskSection
        values={bad}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Максимум 100%/)).toBeInTheDocument();
  });

  it('shows validation error for kill switch threshold below minimum', () => {
    const enabled: RiskSettings = {
      ...risk,
      killSwitchEnabled: true,
      killSwitchThresholdPct: -1,
    };
    render(
      <RiskSection
        values={enabled}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Минимум 1%/)).toBeInTheDocument();
  });

  it('shows validation error for kill switch threshold above maximum', () => {
    const enabled: RiskSettings = {
      ...risk,
      killSwitchEnabled: true,
      killSwitchThresholdPct: 999,
    };
    render(
      <RiskSection
        values={enabled}
        onChange={() => {}}
        onSave={() => {}}
        saving={false}
        dirty={false}
      />,
    );
    expect(screen.getByText(/Максимум 50%/)).toBeInTheDocument();
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
