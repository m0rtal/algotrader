import { useMemo } from 'react';

interface Props {
  symbol: string;
  width?: number;
  height?: number;
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

// Build a series from the same formula Sparkline uses, so we can predict the trend direction.
function seriesFor(symbol: string, n = 9): number[] {
  const seed = hash(symbol);
  const series: number[] = [];
  let v = 50 + (seed % 20);
  for (let i = 0; i < n; i++) {
    v += ((seed >> (i * 3)) & 0xff) / 64 - 2;
    series.push(v);
  }
  return series;
}

export { hash, seriesFor };

export function Sparkline({ symbol, width = 60, height = 16 }: Props) {
  const { points, color } = useMemo(() => {
    const series = seriesFor(symbol);
    const n = series.length;
    const min = Math.min(...series);
    const max = Math.max(...series);
    const range = max - min;
    const step = width / (n - 1);
    const pts = series.map((val, i) => {
      const x = i * step;
      const y = height - 2 - ((val - min) / range) * (height - 4);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return { points: pts.join(' '), color: /* v8 ignore next */ (series[n - 1]! >= series[0]! ? '#26a69a' : '#ef5350') };
  }, [symbol, width, height]);

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}>
      <polyline points={points} fill="none" stroke={color} strokeWidth="1" />
    </svg>
  );
}
