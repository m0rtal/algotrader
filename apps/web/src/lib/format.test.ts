import { describe, expect, it } from 'vitest';
import { formatDate, formatDateTime, formatNum, formatPct, formatRUB, formatTime } from '@lib/format';

describe('formatRUB', () => {
  it('formats positive numbers with ruble sign', () => {
    expect(formatRUB(1234)).toMatch(/1\s*234\s*₽/);
  });
  it('formats zero', () => {
    expect(formatRUB(0)).toMatch(/0\s*₽/);
  });
  it('formats negative with minus before digits', () => {
    expect(formatRUB(-500)).toMatch(/[−-]\s*500\s*₽/);
  });
  it('handles NaN', () => {
    expect(formatRUB(Number.NaN)).toBe('—');
  });
  it('compact mode for big numbers', () => {
    expect(formatRUB(1_500_000, { compact: true })).toContain('млн');
  });
  it('adds + sign when sign option and positive', () => {
    expect(formatRUB(100, { sign: true })).toMatch(/^\+\s*100\s*₽$/);
  });
  it('does not add + when sign option but value is zero', () => {
    expect(formatRUB(0, { sign: true })).toMatch(/^0\s*₽$/);
  });
  it('does not add + when sign option but value is negative', () => {
    expect(formatRUB(-50, { sign: true })).toMatch(/[−-]\s*50\s*₽/);
  });
});

describe('formatPct', () => {
  it('positive with +', () => {
    expect(formatPct(2.4)).toBe('+2.40%');
  });
  it('negative with -', () => {
    expect(formatPct(-1.5)).toBe('-1.50%');
  });
  it('zero without +', () => {
    expect(formatPct(0)).toBe('0.00%');
  });
  it('respects decimals option', () => {
    expect(formatPct(3.14159, { decimals: 0 })).toBe('+3%');
  });
  it('handles NaN', () => {
    expect(formatPct(Number.NaN)).toBe('—');
  });
  it('without sign option for positive', () => {
    expect(formatPct(5, { sign: false })).toBe('5.00%');
  });
});

describe('formatNum', () => {
  it('russian locale grouping', () => {
    const result = formatNum(1234567.89);
    expect(result).toMatch(/1/);
    expect(result).toMatch(/234/);
  });
  it('compact for big numbers', () => {
    expect(formatNum(2_500_000, { compact: true })).toContain('млн');
  });
  it('handles NaN', () => {
    expect(formatNum(Number.NaN)).toBe('—');
  });
  it('uses 2 decimals by default', () => {
    expect(formatNum(3.14159)).toMatch(/3[.,]14/);
  });
  it('respects custom decimals', () => {
    expect(formatNum(3.14159, { decimals: 4 })).toMatch(/3[.,]1416/);
  });
  it('formats negative', () => {
    expect(formatNum(-42)).toMatch(/[−-]\s*42/);
  });
  it('formats zero', () => {
    expect(formatNum(0)).toBe('0');
  });
});

describe('formatTime', () => {
  it('formats ISO time to HH:MM', () => {
    expect(formatTime('2026-09-06T19:34:00+03:00')).toBe('19:34');
  });
  it('handles invalid date', () => {
    expect(formatTime('not-a-date')).toBe('not-a-date');
  });
  it('handles empty string', () => {
    expect(formatTime('')).toBe('');
  });
});

describe('formatDate', () => {
  it('formats ISO date to ru locale', () => {
    const result = formatDate('2026-09-06T19:34:00+03:00');
    expect(result).toMatch(/06/);
    expect(result).toMatch(/09/);
    expect(result).toMatch(/2026/);
  });
  it('handles invalid date', () => {
    expect(formatDate('garbage')).toBe('garbage');
  });
});

describe('formatDateTime', () => {
  it('combines date and time', () => {
    const result = formatDateTime('2026-09-06T19:34:00+03:00');
    expect(result).toContain('19:34');
    expect(result).toMatch(/06/);
  });
  it('handles invalid date', () => {
    expect(formatDateTime('garbage')).toBe('garbage');
  });
});
