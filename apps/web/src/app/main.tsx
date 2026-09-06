import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import '@styles/globals.css';

async function enableMocking() {
  if (!import.meta.env.DEV) return;
  const { worker } = await import('../mocks/browser');
  await worker.start({
    onUnhandledRequest: 'bypass',
    serviceWorker: { url: '/mockServiceWorker.js' },
  });
}

void enableMocking().then(() => {
  const rootEl = document.getElementById('root');
  if (!rootEl) throw new Error('Root element #root not found');
  createRoot(rootEl).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
});
