import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { App } from './App';
import { Providers } from './providers';

describe('App', () => {
  it('renders without crashing', () => {
    render(<App />);
  });

  it('renders the dashboard route by default', () => {
    render(<App />);
    // The dashboard should render with a known element from Topbar
    expect(screen.getByText('ALGOTRADER')).toBeInTheDocument();
  });

  it('renders the global log strip footer on every route', async () => {
    render(<App />);
    // LogStrip fetches /api/logs through MSW; the strip should mount
    // at the App root so it shows up on both Dashboard and Settings.
    const strip = await screen.findByTestId('global-log-strip');
    expect(strip).toBeInTheDocument();
    // Fixed bottom positioning keeps the footer pinned to viewport.
    expect(strip.className).toMatch(/fixed/);
    expect(strip.className).toMatch(/bottom-0/);
  });
});

describe('Providers', () => {
  it('renders its children', () => {
    render(
      <Providers>
        <div data-testid="child">child content</div>
      </Providers>,
    );
    expect(screen.getByTestId('child')).toHaveTextContent('child content');
  });
});
