import { describe, expect, it } from 'vitest';
import { formatNum, formatPct, formatRUB } from '@lib/format';

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
});

describe('formatPct', () => {
  it('positive with +', () => {
    expect(formatPct(2.4)).toBe('+2.40%');
  });
  it('negative with -', () => {
    expect(formatPct(-1.5)).toBe('-1.50%');
  });
  it('respects decimals option', () => {
    expect(formatPct(3.14159, { decimals: 0 })).toBe('+3%');
  });
});

describe('formatNum', () => {
  it('russian locale grouping', () => {
    expect(formatNum(1234567.89)).toMatch(/1/);
    expect(formatNum(1234567.89)).toMatch(/234/);
  });
  it('compact for big numbers', () => {
    expect(formatNum(2_500_000, { compact: true })).toContain('млн');
  });
});
