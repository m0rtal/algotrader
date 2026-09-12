import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useUiStore, type TabId } from '@stores/uiStore';

describe('uiStore', () => {
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
});
