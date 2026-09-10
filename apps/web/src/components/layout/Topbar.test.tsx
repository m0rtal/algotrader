import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { Topbar } from '@components/layout/Topbar';

describe('Topbar', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the brand and a Sandbox label', () => {
    render(<Topbar />);
    expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
    expect(screen.getByText('Sandbox')).toBeInTheDocument();
  });

  it('renders a link to settings', () => {
    render(<Topbar />);
    const link = screen.getByRole('link', { name: /settings/i });
    expect(link.getAttribute('href')).toBe('/settings');
  });
});
