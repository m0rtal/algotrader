import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import '@styles/globals.css';

// MSW dev worker is disabled in this build: handlers only passthrough to
// the real backend, and Vite's /api proxy already serves same-origin
// requests, so the worker adds an SW-blocking startup step with no real
// value. Re-enable here if dev-only mock routes are ever introduced.
const ENABLE_MSW = false;

async function enableMocking() {
  if (!ENABLE_MSW || !import.meta.env.DEV) return;
  const { worker } = await import('../mocks/browser');
  await worker.start({
    onUnhandledRequest: 'bypass',
    serviceWorker: { url: '/mockServiceWorker.js' },
  });
}

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('Root element #root not found');
createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

void enableMocking();
