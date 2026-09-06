import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DangerZone } from '@features/settings/DangerZone';
import { Toast } from '@features/settings/Toast';
import { Section } from '@features/settings/Section';

describe('DangerZone', () => {
  it('renders the danger heading and reset button', () => {
    render(<DangerZone onReset={() => {}} />);
    expect(screen.getByText('Опасная зона')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сбросить настройки' })).toBeInTheDocument();
  });

  it('shows confirm dialog on click', async () => {
    render(<DangerZone onReset={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить настройки' }));
    await waitFor(() => {
      expect(screen.getByText('Сбросить все настройки к defaults?')).toBeInTheDocument();
    });
  });

  it('fires onReset on confirm', async () => {
    const onReset = vi.fn();
    render(<DangerZone onReset={onReset} />);
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить настройки' }));
    await waitFor(() => {
      expect(screen.getByText('Сбросить все настройки к defaults?')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }));
    expect(onReset).toHaveBeenCalled();
  });

  it('does not fire onReset on cancel', async () => {
    const onReset = vi.fn();
    render(<DangerZone onReset={onReset} />);
    fireEvent.click(screen.getByRole('button', { name: 'Сбросить настройки' }));
    await waitFor(() => {
      expect(screen.getByText('Сбросить все настройки к defaults?')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(onReset).not.toHaveBeenCalled();
  });

  it('shows busy state when busy=true', () => {
    render(<DangerZone onReset={() => {}} busy />);
    expect(screen.getByText('Сброс…')).toBeInTheDocument();
  });
});

describe('Toast', () => {
  it('renders the message and dismisses on click', () => {
    const onDismiss = vi.fn();
    render(<Toast message="Сохранено" tone="ok" onDismiss={onDismiss} />);
    expect(screen.getByText('Сохранено')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Закрыть'));
    expect(onDismiss).toHaveBeenCalled();
  });

  it.each([
    ['ok', 'bg-green'],
    ['err', 'bg-red'],
    ['warn', 'bg-amber'],
    ['info', 'bg-accent'],
  ] as const)('applies tone class for %s', (tone, expected) => {
    const { container } = render(<Toast message="x" tone={tone} onDismiss={() => {}} />);
    expect(container.querySelector(`.${expected}`)).toBeTruthy();
  });
});

describe('Section', () => {
  it('renders title, description, and children', () => {
    render(
      <Section title="Test" description="A description">
        <div>child1</div>
      </Section>,
    );
    expect(screen.getByText('Test')).toBeInTheDocument();
    expect(screen.getByText('A description')).toBeInTheDocument();
    expect(screen.getByText('child1')).toBeInTheDocument();
  });

  it('renders Save button when onSave provided', () => {
    render(
      <Section title="t" onSave={vi.fn()} dirty saving={false}>
        <div />
      </Section>,
    );
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
  });

  it('disables Save when not dirty', () => {
    render(
      <Section title="t" onSave={vi.fn()} dirty={false} saving={false}>
        <div />
      </Section>,
    );
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('shows saving label when saving=true', () => {
    render(
      <Section title="t" onSave={vi.fn()} dirty saving>
        <div />
      </Section>,
    );
    expect(screen.getByText('Сохранение…')).toBeInTheDocument();
  });

  it('does not render Save button when onSave omitted', () => {
    render(
      <Section title="t">
        <div />
      </Section>,
    );
    expect(screen.queryByRole('button', { name: 'Сохранить' })).not.toBeInTheDocument();
  });
});
