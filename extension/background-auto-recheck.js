'use strict';

(function exposeBackgroundAutoRecheck(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.BackgroundAutoRecheck = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  function create(dependencies = {}) {
    const chrome = dependencies.chrome;
    const tabs = dependencies.tabs;
    const now = typeof dependencies.now === 'function' ? dependencies.now : Date.now;
    const setTimer = dependencies.setTimeout || globalThis.setTimeout.bind(globalThis);
    const clearTimer = dependencies.clearTimeout || globalThis.clearTimeout.bind(globalThis);
    const recoverySweepIntervalMs = Math.max(
      20000,
      Number(dependencies.recoverySweepIntervalMs || 60000),
    );
    const timers = new Map();
    let lastHeartbeatAt = Number.NEGATIVE_INFINITY;

    if (!chrome || !chrome.tabs || typeof chrome.tabs.sendMessage !== 'function') {
      throw new Error('BackgroundAutoRecheck requires chrome.tabs.sendMessage');
    }
    if (!tabs || typeof tabs.keys !== 'function' || typeof tabs.has !== 'function') {
      throw new Error('BackgroundAutoRecheck requires tabs Map');
    }

    function clear(tabId) {
      const targetTabId = Number(tabId);
      const timer = timers.get(targetTabId);
      if (timer) clearTimer(timer);
      timers.delete(targetTabId);
    }

    function send(tabId) {
      const targetTabId = Number(tabId);
      if (!targetTabId || !tabs.has(targetTabId)) return false;
      chrome.tabs.sendMessage(targetTabId, { type: 'auto-recheck' }).catch(() => {});
      return true;
    }

    function schedule(tabId, delayMs) {
      const targetTabId = Number(tabId);
      if (!targetTabId || !tabs.has(targetTabId)) return false;
      clear(targetTabId);
      const delay = Math.max(50, Math.min(5000, Number(delayMs) || 500));
      const timer = setTimer(() => {
        timers.delete(targetTabId);
        send(targetTabId);
      }, delay);
      if (timer && typeof timer.unref === 'function') timer.unref();
      timers.set(targetTabId, timer);
      return true;
    }

    function heartbeat(enabled) {
      if (!enabled || tabs.size === 0) return false;
      const timestamp = now();
      if (timestamp - lastHeartbeatAt < recoverySweepIntervalMs) return false;
      lastHeartbeatAt = timestamp;
      for (const tabId of tabs.keys()) send(tabId);
      return true;
    }

    return { clear, heartbeat, schedule };
  }

  return { create };
});
