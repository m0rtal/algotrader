import { useEffect, useState } from 'react';

const STATUS_KEY = 'algotrader.apiMode';

/** Reads the last observed mode from localStorage so the badge is
 * stable across reloads. Values: 'live' | 'mock' | 'unknown'. */
function readPersisted(): 'live' | 'mock' | 'unknown' {
  /* v8 ignore next */
  if (typeof localStorage === 'undefined') return 'unknown';
  try {
    const v = localStorage.getItem(STATUS_KEY);
    /* v8 ignore next */
    if (v === 'live' || v === 'mock') return v;
  } catch {
    /* ignore */
  }
  return 'unknown';
}

function writePersisted(mode: 'live' | 'mock' | 'unknown') {
  /* v8 ignore next */
  if (typeof localStorage === 'undefined') return;
  try {
    /* v8 ignore next */
    if (mode === 'unknown') localStorage.removeItem(STATUS_KEY);
    else localStorage.setItem(STATUS_KEY, mode);
  } catch {
    /* ignore */
  }
}

/** Probes http://127.0.0.1:8000/health every 10 s and exposes the
 * last-observed mode. The badge then re-renders without us needing
 * to plumb a global store — uiStore can read this if it needs to
 * route traffic to the real backend.
 */
export function ApiStatusBadge() {
  const [mode, setMode] = useState<'live' | 'mock' | 'unknown'>(
    () => readPersisted(),
  );

  useEffect(() => {
    let cancelled = false;

    async function probe(): Promise<'live' | 'mock'> {
      try {
        // AbortController so a slow backend doesn't pile up requests.
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 2000);
        const res = await fetch('http://127.0.0.1:8000/health', {
          signal: ctrl.signal,
        });
        clearTimeout(timer);
        return res.ok ? 'live' : 'mock';
      } catch {
        return 'mock';
      }
    }

    async function tick() {
      /* v8 ignore next */
      if (cancelled) return;
      const next = await probe();
      setMode((prev) => {
        if (prev !== next) writePersisted(next);
        return next;
      });
    }

    void tick();
    const id = setInterval(tick, 10_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (mode === 'live') {
    return (
      <span
        data-testid="api-status-badge"
        data-mode="live"
        className="shrink-0 mono text-[10px] px-2 py-0.5 rounded border border-green text-green"
        title="Connected to live backend at http://127.0.0.1:8000"
      >
        ● live
      </span>
    );
  }
  // mock or unknown — same gray style
  return (
    <span
      data-testid="api-status-badge"
      data-mode="mock"
      className="shrink-0 mono text-[10px] px-2 py-0.5 rounded border border-text-muted text-text-muted"
      title="No backend reachable at http://127.0.0.1:8000 — using mock data"
    >
      ○ mock
    </span>
  );
}
