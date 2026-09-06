import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Sparkline } from '@components/charts/Sparkline';

describe('Sparkline', () => {
  it('renders an svg with a polyline', () => {
    const { container } = render(<Sparkline symbol="SBER" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(svg?.getAttribute('width')).toBe('60');
    expect(svg?.getAttribute('height')).toBe('16');
    const polyline = container.querySelector('polyline');
    expect(polyline).toBeTruthy();
    const points = polyline?.getAttribute('points') ?? '';
    expect(points.split(' ').length).toBe(9);
  });

  it('uses the same color (green) for the same symbol across renders (deterministic)', () => {
    const { container: c1 } = render(<Sparkline symbol="GAZP" />);
    const { container: c2 } = render(<Sparkline symbol="GAZP" />);
    const stroke1 = c1.querySelector('polyline')?.getAttribute('stroke');
    const stroke2 = c2.querySelector('polyline')?.getAttribute('stroke');
    expect(stroke1).toBe(stroke2);
  });

  it('produces different point sequences for different symbols', () => {
    const { container: c1 } = render(<Sparkline symbol="SBER" />);
    const { container: c2 } = render(<Sparkline symbol="GAZP" />);
    const p1 = c1.querySelector('polyline')?.getAttribute('points') ?? '';
    const p2 = c2.querySelector('polyline')?.getAttribute('points') ?? '';
    expect(p1).not.toBe(p2);
  });

  it('honors custom width and height', () => {
    const { container } = render(<Sparkline symbol="YNDX" width={100} height={32} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('100');
    expect(svg?.getAttribute('height')).toBe('32');
  });

  it('renders a stroke color (green or red)', () => {
    // The exact color depends on the deterministic hash of the symbol,
    // so we just assert that a known-good stroke color is in use.
    const validColors = new Set(['#26a69a', '#ef5350']);
    for (const s of ['SBER', 'GAZP', 'YNDX', 'X', 'Y']) {
      const { container } = render(<Sparkline symbol={s} />);
      const stroke = container.querySelector('polyline')?.getAttribute('stroke');
      expect(stroke).not.toBeNull();
      expect(validColors.has(stroke ?? '')).toBe(true);
    }
  });

  it('produces 9 data points regardless of symbol', () => {
    for (const s of ['AAA', 'BBBB', 'CC']) {
      const { container } = render(<Sparkline symbol={s} />);
      const pts = (container.querySelector('polyline')?.getAttribute('points') ?? '').split(' ');
      expect(pts.length).toBe(9);
    }
  });

  it('produces a y-coordinate that varies (verifies series is non-constant)', () => {
    // A non-constant series exercises the y = ... * (height-4) calculation branch.
    const { container } = render(<Sparkline symbol="SBER" width={60} height={16} />);
    const points =
      (container.querySelector('polyline')?.getAttribute('points') ?? '').split(' ');
    const ys = points.map((p) => parseFloat(p.split(',')[1]!));
    const uniqueYs = new Set(ys);
    expect(uniqueYs.size).toBeGreaterThan(1);
  });

  it('color matches the predicted trend direction (covers both >= branches)', async () => {
    const { seriesFor } = await import('@components/charts/Sparkline');
    for (let i = 0; i < 200 && true; i++) {
      const sym = `T${i}`;
      const series = seriesFor(sym);
      const expected = series[series.length - 1]! >= series[0]! ? '#26a69a' : '#ef5350';
      const { container } = render(<Sparkline symbol={sym} />);
      const stroke = container.querySelector('polyline')?.getAttribute('stroke');
      expect(stroke).toBe(expected);
      if (expected === '#ef5350') break; // found a downtrend symbol; the > branch was covered
    }
  });
});
