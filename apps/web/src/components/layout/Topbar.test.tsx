import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Topbar } from '@components/layout/Topbar';

describe('Topbar', () => {
  it('renders the brand name', () => {
    render(<Topbar />);
    expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
  });

  it('renders the MOEX tag', () => {
    render(<Topbar />);
    expect(screen.getAllByText(/MOEX/).length).toBeGreaterThan(0);
  });

  it('shows session status as CLOSED', () => {
    render(<Topbar />);
    expect(screen.getByText('CLOSED')).toBeInTheDocument();
  });

  it('shows IMOEX value', () => {
    render(<Topbar />);
    expect(screen.getByText('3 142.8')).toBeInTheDocument();
  });

  it('shows update time in МСК', () => {
    render(<Topbar />);
    expect(screen.getByText(/МСК/)).toBeInTheDocument();
  });

  it('renders the Sandbox badge', () => {
    render(<Topbar />);
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
  });
});
