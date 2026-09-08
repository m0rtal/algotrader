import { describe, expect, it, beforeEach } from 'vitest';

beforeEach(() => {
  localStorage.clear();
});

describe('mocks/handlers — backfill coverage', () => {
  it('start persists state and returns 202', async () => {
    const res = await fetch('/api/admin/backfill/start', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ history_years: 2 }),
    });
    expect(res.status).toBe(202);
    const stored = JSON.parse(
      localStorage.getItem('algotrader.mock_backfill_state')!,
    );
    expect(stored.state).toBe('backfilling');
    expect(stored.history_years).toBe(2);
  });

  it('status falls back to idle when storage empty', async () => {
    const res = await fetch('/api/admin/backfill/status');
    const body = await res.json();
    expect(body.state).toBe('idle');
  });

  it('stop updates stored state', async () => {
    await fetch('/api/admin/backfill/start', { method: 'POST' });
    const res = await fetch('/api/admin/backfill/stop', {
      method: 'POST',
    });
    const body = await res.json();
    expect(body.state).toBe('stopping');
    const stored = JSON.parse(
      localStorage.getItem('algotrader.mock_backfill_state')!,
    );
    expect(stored.state).toBe('idle');
  });

  it('events returns an event-stream response', async () => {
    const res = await fetch('/api/admin/backfill/events');
    expect(res.headers.get('content-type')).toContain('text/event-stream');
  });

  it('readBackfillState handles malformed JSON gracefully', async () => {
    localStorage.setItem('algotrader.mock_backfill_state', '{not json');
    const res = await fetch('/api/admin/backfill/status');
    const body = await res.json();
    expect(body.state).toBe('idle');
  });

  it('start accepts empty body (history_years defaults to 1)', async () => {
    await fetch('/api/admin/backfill/start', { method: 'POST' });
    const stored = JSON.parse(
      localStorage.getItem('algotrader.mock_backfill_state')!,
    );
    expect(stored.history_years).toBe(1);
  });
});
