import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ConfirmDialog } from '@features/settings/ConfirmDialog';

describe('ConfirmDialog', () => {
  it('renders nothing when open=false', () => {
    const { container } = render(
      <ConfirmDialog open={false} title="t" body="b" onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders title and body when open', () => {
    render(<ConfirmDialog open title="Удалить?" body="Точно?" onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText('Удалить?')).toBeInTheDocument();
    expect(screen.getByText('Точно?')).toBeInTheDocument();
  });

  it('fires onCancel when backdrop is clicked directly', () => {
    let cancelCount = 0;
    const { container } = render(
      <ConfirmDialog open title="t" body="b" onConfirm={() => {}} onCancel={() => cancelCount++} />,
    );
    fireEvent.click(container.firstChild as HTMLElement);
    expect(cancelCount).toBe(1);
  });

  it('does not fire onCancel when clicking inside dialog', () => {
    let cancelCount = 0;
    render(
      <ConfirmDialog open title="t" body="body-text" onConfirm={() => {}} onCancel={() => cancelCount++} />,
    );
    fireEvent.click(screen.getByText('body-text'));
    expect(cancelCount).toBe(0);
  });

  it('fires onConfirm when confirm button is clicked', () => {
    let confirmCount = 0;
    render(
      <ConfirmDialog open title="t" body="b" onConfirm={() => confirmCount++} onCancel={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(confirmCount).toBe(1);
  });

  it('fires onCancel when cancel button is clicked', () => {
    let cancelCount = 0;
    render(
      <ConfirmDialog open title="t" body="b" onConfirm={() => {}} onCancel={() => cancelCount++} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(cancelCount).toBe(1);
  });

  it('uses danger styling when danger=true', () => {
    render(
      <ConfirmDialog
        open
        title="t"
        body="b"
        danger
        confirmLabel="Удалить навсегда"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Удалить навсегда' });
    expect(btn.className).toContain('bg-red');
  });

  it('uses default (non-danger) styling when danger=false', () => {
    render(
      <ConfirmDialog
        open
        title="t"
        body="b"
        confirmLabel="Ок"
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    const btn = screen.getByRole('button', { name: 'Ок' });
    expect(btn.className).toContain('bg-accent');
    expect(btn.className).not.toContain('bg-red');
  });
});
