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
