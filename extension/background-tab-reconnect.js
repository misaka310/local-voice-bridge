'use strict';

(function initBackgroundTabReconnect(global) {
  const CONTENT_SCRIPT_FILES = Object.freeze([
    'live-browser-core.js',
    'live-content-controller.js',
    'prompt-input-core.js',
    'delivery-id-core.js',
    'content-text-core.js',
    'assistant-source-filter.js',
    'assistant-text-extractor.js',
    'auto-speech-controller.js',
    'content-settings.js',
    'content-mutation-filter.js',
    'content-dom-observer.js',
    'content-completion-marker.js',
    'content-conversation-bridge.js',
    'content-audio-player.js',
    'content-message-router.js',
    'content.js',
  ]);

  function create(ctx) {
    const timeoutMs = Math.max(10, Number(ctx.timeoutMs) || 2500);

    function settleWithin(promise) {
      return Promise.race([
        Promise.resolve(promise),
        new Promise((_, reject) => setTimeout(() => reject(new Error('reconnect-timeout')), timeoutMs)),
      ]);
    }

    async function reconnectTab(tab) {
      const tabId = Number(tab && tab.id);
      if (!Number.isInteger(tabId) || tabId <= 0) return false;
      if (ctx.reconnectingTabs.has(tabId)) return ctx.reconnectingTabs.get(tabId);
      const attempt = (async () => {
        try {
          const response = await settleWithin(ctx.chrome.tabs.sendMessage(tabId, { type: 'bridge-reconnect' }));
          return Boolean(response && response.ok === true);
        } catch (error) {
          if (String(error && error.message || error) === 'reconnect-timeout') return false;
        }
        if (!ctx.chrome.scripting || typeof ctx.chrome.scripting.executeScript !== 'function') return false;
        try {
          await settleWithin(ctx.chrome.scripting.executeScript({
            target: { tabId },
            files: CONTENT_SCRIPT_FILES,
          }));
          const response = await settleWithin(ctx.chrome.tabs.sendMessage(tabId, { type: 'bridge-reconnect' }));
          return Boolean(response && response.ok === true);
        } catch (_error) {
          return false;
        }
      })();
      ctx.reconnectingTabs.set(tabId, attempt);
      try {
        return await attempt;
      } finally {
        ctx.reconnectingTabs.delete(tabId);
      }
    }

    async function reconnectOpenTabs() {
      let openTabs = [];
      try {
        openTabs = await settleWithin(ctx.chrome.tabs.query({ url: ctx.tabPatterns }));
      } catch (_error) {
        return false;
      }
      const openTabIds = new Set(openTabs
        .map((tab) => Number(tab && tab.id))
        .filter((id) => Number.isInteger(id) && id > 0));
      for (const tabId of Array.from(ctx.tabs.keys())) {
        if (!openTabIds.has(tabId)) ctx.tabs.delete(tabId);
      }
      ctx.ensureOwner();
      await Promise.all(openTabs.map(reconnectTab));
      ctx.broadcastState();
      return true;
    }

    return { reconnectTab, reconnectOpenTabs };
  }

  global.BackgroundTabReconnect = Object.freeze({ CONTENT_SCRIPT_FILES, create });
})(globalThis);
