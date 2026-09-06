import { useEffect, useRef } from 'react';
import {
  AreaSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type AreaData,
  type Time,
} from 'lightweight-charts';

interface Props {
  data: number[];
  color?: string;
  height?: number;
}

export function EquityCurve({ data, color = '#26a69a', height = 180 }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const chart = createChart(el, {
      width: el.clientWidth,
      height,
      layout: { background: { color: 'transparent' }, textColor: '#8b91a3' },
      grid: { vertLines: { color: '#232838' }, horzLines: { color: '#232838' } },
      rightPriceScale: { borderColor: '#232838' },
      timeScale: { borderColor: '#232838' },
    });
    const series: ISeriesApi<'Area'> = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: `${color}4d`,
      bottomColor: `${color}00`,
      lineWidth: 2,
    });
    chartRef.current = chart;
    seriesRef.current = series;

    const ro = new ResizeObserver(() => {
      if (chartRef.current) chartRef.current.applyOptions({ width: el.clientWidth });
    });
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [color, height]);

  useEffect(() => {
    if (!seriesRef.current) return;
    const series = seriesRef.current;
    const lineData: AreaData[] = data.map((value, i) => ({
      time: (i + 1) as Time,
      value,
    }));
    series.setData(lineData);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={containerRef} style={{ width: '100%', height }} />;
}
