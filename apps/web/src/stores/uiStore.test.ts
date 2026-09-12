import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useUiStore, type TabId } from '@stores/uiStore';

describe('uiStore', () => {
  // The store is a module-level singleton. Without a reset, tests
  // that mutate activeTab leak state into the next renderHook call.
  // beforeEach clears both the in-memory store and localStorage so
  // every test sees the documented initial state.
  beforeEach(() => {
    localStorage.clear();
    useUiStore.setState({
      activeTab: 'signals',
      selectedTicker: null,
      sidebarOpen: false,
      railOpen: false,
    });
  });
  afterEach(() => {
    localStorage.clear();
  });

  it('initial state: signals tab, no selected ticker', () => {
    const { result } = renderHook(() => useUiStore());
    expect(result.current.activeTab).toBe('signals');
    expect(result.current.selectedTicker).toBeNull();
  });

  it('setActiveTab updates active tab', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.setActiveTab('trades'));
    expect(result.current.activeTab).toBe('trades');
  });

  it('openTicker sets selectedTicker', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.openTicker('SBER'));
    expect(result.current.selectedTicker).toBe('SBER');
  });

  it('openTicker replaces previous selectedTicker', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.openTicker('SBER'));
    act(() => result.current.openTicker('GAZP'));
    expect(result.current.selectedTicker).toBe('GAZP');
  });

  it('closeTicker clears selectedTicker', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.openTicker('SBER'));
    act(() => result.current.closeTicker());
    expect(result.current.selectedTicker).toBeNull();
  });

  it('closeTicker is safe to call when no ticker is selected', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.closeTicker());
    expect(result.current.selectedTicker).toBeNull();
  });

  it('setActiveTab and openTicker are independent', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.setActiveTab('backtest'));
    act(() => result.current.openTicker('YNDX'));
    expect(result.current.activeTab).toBe('backtest');
    expect(result.current.selectedTicker).toBe('YNDX');
  });

  it('all five tab ids are valid', () => {
    const tabs: TabId[] = ['signals', 'trades', 'portfolio', 'backtest', 'storage'];
    const { result } = renderHook(() => useUiStore());
    for (const tab of tabs) {
      act(() => result.current.setActiveTab(tab));
      expect(result.current.activeTab).toBe(tab);
    }
  });

  it('initial state: sidebars closed', () => {
    const { result } = renderHook(() => useUiStore());
    expect(result.current.sidebarOpen).toBe(false);
    expect(result.current.railOpen).toBe(false);
  });

  it('toggleSidebar flips sidebarOpen', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.toggleSidebar());
    expect(result.current.sidebarOpen).toBe(true);
    act(() => result.current.toggleSidebar());
    expect(result.current.sidebarOpen).toBe(false);
  });

  it('toggleRail flips railOpen', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.toggleRail());
    expect(result.current.railOpen).toBe(true);
  });

  it('closeSidebars resets both', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.toggleSidebar());
    act(() => result.current.toggleRail());
    act(() => result.current.closeSidebars());
    expect(result.current.sidebarOpen).toBe(false);
    expect(result.current.railOpen).toBe(false);
  });

  it('openTicker closes both sidebars (mobile drawer UX)', () => {
    const { result } = renderHook(() => useUiStore());
    act(() => result.current.toggleSidebar());
    act(() => result.current.toggleRail());
    act(() => result.current.openTicker('SBER'));
    expect(result.current.sidebarOpen).toBe(false);
    expect(result.current.railOpen).toBe(false);
    expect(result.current.selectedTicker).toBe('SBER');
  });

  it('persists activeTab across hook remounts (localStorage)', () => {
    // Set a non-default tab and confirm the persisted storage
    // actually holds it; re-creating the hook without partialize
    // also picks it up via the persist middleware.
    localStorage.clear();
    const first = renderHook(() => useUiStore());
    act(() => first.result.current.setActiveTab('backfill'));
    const raw = localStorage.getItem('algotrader.ui');
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.activeTab).toBe('backfill');
    // Transient fields MUST NOT be persisted — refresh should land
    // the operator on a clean dashboard.
    expect(parsed.state.selectedTicker).toBeUndefined();
    expect(parsed.state.sidebarOpen).toBeUndefined();
    expect(parsed.state.railOpen).toBeUndefined();
    first.unmount();
  });

  it('rehydrates activeTab from a previously written storage entry', () => {
    // Seed localStorage BEFORE the hook is first mounted so the
    // persist middleware picks the value up during module init.
    localStorage.clear();
    localStorage.setItem(
      'algotrader.ui',
      JSON.stringify({ state: { activeTab: 'portfolio' }, version: 1 }),
    );
    // Force the persist middleware to re-read localStorage instead
    // of returning the in-memory cached value.
    useUiStore.persist.rehydrate();
    const second = renderHook(() => useUiStore());
    expect(second.result.current.activeTab).toBe('portfolio');
  });

  it('clears the persisted tab when storage is missing or corrupt', () => {
    localStorage.clear();
    localStorage.setItem('algotrader.ui', '{not-json');
    const second = renderHook(() => useUiStore());
    // Falls back to the initial value rather than throwing.
    expect(second.result.current.activeTab).toBe('signals');
  });
});
