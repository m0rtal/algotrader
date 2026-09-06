import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const seriesMock = { setData: vi.fn() };
const chartMock = {
  addSeries: vi.fn(() => seriesMock),
  remove: vi.fn(),
  applyOptions: vi.fn(),
  timeScale: () => ({ fitContent: vi.fn() }),
};

vi.mock('lightweight-charts', () => ({
  createChart: vi.fn(() => chartMock),
  AreaSeries: function AreaSeries() {},
}));

// Import after vi.mock
const { EquityCurve } = await import('@components/charts/EquityCurve');

describe('EquityCurve', () => {
  beforeEach(() => {
    chartMock.addSeries.mockClear();
    chartMock.remove.mockClear();
    chartMock.applyOptions.mockClear();
    seriesMock.setData.mockClear();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders a container div', () => {
    const { container } = render(<EquityCurve data={[1, 2, 3]} />);
    const div = container.querySelector('div');
    expect(div).toBeTruthy();
  });

  it('calls createChart on mount', () => {
    render(<EquityCurve data={[1, 2, 3]} />);
    expect(chartMock.addSeries).toHaveBeenCalled();
  });

  it('creates an Area series with the provided color', () => {
    render(<EquityCurve data={[1, 2, 3]} color="#ff0000" />);
    expect(chartMock.addSeries).toHaveBeenCalledWith(
      expect.any(Function),
      expect.objectContaining({ lineColor: '#ff0000' }),
    );
  });

  it('passes data to the series on mount', () => {
    const data = [100, 110, 120];
    render(<EquityCurve data={data} />);
    expect(seriesMock.setData).toHaveBeenCalled();
    const calledWith = seriesMock.setData.mock.calls[0]![0] as Array<{ value: number }>;
    expect(calledWith.length).toBe(3);
    expect(calledWith[0]!.value).toBe(100);
    expect(calledWith[2]!.value).toBe(120);
  });

  it('updates the series when data changes', () => {
    const { rerender } = render(<EquityCurve data={[1, 2, 3]} />);
    expect(seriesMock.setData).toHaveBeenCalledTimes(1);
    rerender(<EquityCurve data={[4, 5, 6, 7]} />);
    expect(seriesMock.setData).toHaveBeenCalledTimes(2);
    const lastCall = seriesMock.setData.mock.calls[1]![0] as Array<{ value: number }>;
    expect(lastCall.map((p) => p.value)).toEqual([4, 5, 6, 7]);
  });

  it('removes the chart on unmount', () => {
    const { unmount } = render(<EquityCurve data={[1, 2]} />);
    unmount();
    expect(chartMock.remove).toHaveBeenCalled();
  });

  it('uses default color when none provided', () => {
    render(<EquityCurve data={[1, 2]} />);
    expect(chartMock.addSeries).toHaveBeenCalledWith(
      expect.any(Function),
      expect.objectContaining({ lineColor: '#26a69a' }),
    );
  });

  it('ResizeObserver callback is installed on mount', () => {
    let savedCallback: (() => void) | null = null;
    const observed: Element[] = [];
    class ResizeObserverStub {
      callback: () => void;
      constructor(cb: () => void) {
        this.callback = cb;
        savedCallback = cb;
      }
      observe(el: Element) {
        observed.push(el);
      }
      unobserve() {}
      disconnect() {}
    }
    const OriginalRO = (globalThis as { ResizeObserver: unknown }).ResizeObserver;
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
    try {
      const { unmount } = render(<EquityCurve data={[1, 2]} />);
      expect(observed.length).toBe(1);
      // Truthy branch: chartRef.current is set, so applyOptions is called
      chartMock.applyOptions.mockClear();
      savedCallback!();
      expect(chartMock.applyOptions).toHaveBeenCalled();
      // Falsy branch: after unmount, chartRef.current is null; the callback no-ops
      unmount();
      chartMock.applyOptions.mockClear();
      savedCallback!();
      // The callback should NOT call applyOptions when chartRef is null
      expect(chartMock.applyOptions).not.toHaveBeenCalled();
    } finally {
      (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = OriginalRO;
    }
  });
});
