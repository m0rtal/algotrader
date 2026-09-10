import { Providers } from './providers';
import { Router } from './router';
import { LogStrip } from '@components/layout/LogStrip';

export function App() {
  return (
    <Providers>
      <Router />
      {/* Global footer: pinned to viewport bottom, visible on every
          page. The current page must leave room for it (Dashboard
          already uses pb-10 on the scrollable area). */}
      <LogStrip />
    </Providers>
  );
}
