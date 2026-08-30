'use strict';

(function exposeBackgroundExternalState(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundExternalState = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  function normalizeTabId(value) {
    const number = Number(value || 0);
    return Number.isFinite(number) && number > 0 ? Math.trunc(number) : 0;
  }

  function tabTitle(tabs, tabId, fallback = '') {
    const normalizedId = normalizeTabId(tabId);
    const info = normalizedId && tabs && typeof tabs.get === 'function' ? tabs.get(normalizedId) : null;
    return String(info && info.title || fallback || '').trim().slice(0, 200);
  }

  function buildContext({ tabs, manualTargetTabId, currentItem, lastPlayedItem }) {
    const manualId = normalizeTabId(manualTargetTabId);
    const source = currentItem && typeof currentItem === 'object'
      ? currentItem
      : lastPlayedItem && typeof lastPlayedItem === 'object'
        ? lastPlayedItem
        : null;
    const sourceId = normalizeTabId(source && source.tabId);
    return {
      autoScopeTabs: tabs && typeof tabs.size === 'number' ? Math.max(0, tabs.size) : 0,
      manualTargetTabId: manualId,
      manualTargetTitle: tabTitle(tabs, manualId),
      playbackSourceTabId: sourceId,
      playbackSourceTitle: tabTitle(tabs, sourceId, source && source.tabTitle),
    };
  }

  return { buildContext };
});
