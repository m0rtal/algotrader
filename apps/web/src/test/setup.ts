import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, beforeAll } from 'vitest';
import { server } from '../mocks/server';

class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom does not implement ResizeObserver
(globalThis as unknown as { ResizeObserver: typeof ResizeObserverMock }).ResizeObserver =
  ResizeObserverMock;

// We deliberately do NOT mock @lib/api globally. Tests that need canned
// data install MSW handlers via server.use(http.get(...)); tests that
// need error states install error handlers the same way. The dev MSW
// handler set is a strict passthrough to the real backend; in test
// env it would 503 everything, so the per-test server.use() is the
// only way to get data into the query.

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
