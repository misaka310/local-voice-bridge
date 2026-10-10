'use strict';

(function initBackgroundTabReconnect(global) {
  const CONTENT_SCRIPT_FILES = Object.freeze([
    'background-tab-update-watcher.js',
    'live-browser-core.js',
    'live-content-controller.js',
    'prompt-input-core.js',
    'delivery-id-core.js',
    'content-text-core.js',
    'assistant-source-filter.js',
    'assistant-text-extractor.js',
    'generation-completion-evidence.js',
    'auto-speech-controller.js',
    'content-settings.js',
    'content-mic-keepalive.js',
    'content-mutation-filter.js',
    'content-dom-observer.js',
    'content-completion-marker.js',
    'content-conversation-bridge.js',
    'content-audio-player.js',
    'content-message-router.js',
    'content.js',
  ]);
  const UPDATE_WATCHER_FILE = 'background-tab-update-watcher.js';

  function create(ctx) {
    const timeoutMs = Math.max(10, Number(ctx.timeoutMs) || 2500);
    const contentScriptVersion = String(ctx.contentScriptVersion || '');
    const pendingContentUpdateTabs = ctx.pendingContentUpdateTabs || new Set();
    const managedRefreshTabs = ctx.managedRefreshTabs || new Set();

    function settleWithin(promise) {
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('reconnect-timeout')), timeoutMs);
        Promise.resolve(promise).then((value) => {
          clearTimeout(timer);
          resolve(value);
        }, (error) => {
          clearTimeout(timer);
          reject(error);
        });
      });
    }

    function hasCurrentContentScript(response) {
      if (!response || response.ok !== true) return false;
      return !contentScriptVersion || String(response.contentScriptVersion || '') === contentScriptVersion;
    }

    async function clearManagedRefresh(tabId) {
      managedRefreshTabs.delete(tabId);
      if (!ctx.persistManagedRefreshTabs) return;
      try { await ctx.persistManagedRefreshTabs(); } catch (_error) {}
    }

    async function ensureUpdateWatcher(tabId) {
      if (!ctx.chrome.scripting || typeof ctx.chrome.scripting.executeScript !== 'function') return false;
      try {
        await settleWithin(ctx.chrome.scripting.executeScript({
          target: { tabId },
          files: [UPDATE_WATCHER_FILE],
        }));
        const response = await settleWithin(ctx.chrome.tabs.sendMessage(tabId, {
          type: 'bridge-watch-for-update',
          contentScriptVersion,
        }));
        return Boolean(response && response.ok === true);
      } catch (_error) {
        return false;
      }
    }

    async function safeRefreshTab(tabId, contentScriptVersionFromPage = contentScriptVersion) {
      if (!Number.isInteger(tabId) || tabId <= 0) return false;
      if (!contentScriptVersion || contentScriptVersionFromPage !== contentScriptVersion) return false;
      if (ctx.isTabPlaying && ctx.isTabPlaying(tabId)) return false;
      if (ctx.hasPendingPlayback && ctx.hasPendingPlayback(tabId)) return false;
      if (!ctx.chrome.tabs || typeof ctx.chrome.tabs.reload !== 'function') return false;
      try {
        const tab = await settleWithin(ctx.chrome.tabs.get(tabId));
        if (!tab || tab.active !== false || tab.discarded === true) return false;
        managedRefreshTabs.add(tabId);
        try {
          if (ctx.persistManagedRefreshTabs) await ctx.persistManagedRefreshTabs();
        } catch (_error) {
          await clearManagedRefresh(tabId);
          return false;
        }

        const currentTab = await settleWithin(ctx.chrome.tabs.get(tabId));
        if (!currentTab || currentTab.active !== false || currentTab.discarded === true
          || (ctx.isTabPlaying && ctx.isTabPlaying(tabId))
          || (ctx.hasPendingPlayback && ctx.hasPendingPlayback(tabId))) {
          await clearManagedRefresh(tabId);
          return false;
        }
        const result = await settleWithin(ctx.chrome.scripting.executeScript({
          target: { tabId },
          func: () => Boolean(
            globalThis.BackgroundTabUpdateWatcher
            && globalThis.BackgroundTabUpdateWatcher.inspect(document).safe,
          ),
        }));
        const probe = Array.isArray(result) ? result.find((item) => item && item.frameId === 0) : null;
        if (!probe || probe.result !== true) {
          await clearManagedRefresh(tabId);
          return false;
        }
        await settleWithin(ctx.chrome.tabs.reload(tabId));
        return true;
      } catch (_error) {
        await clearManagedRefresh(tabId);
        return false;
      }
    }

    async function handlePageReadyForUpdate(tab, version) {
      const tabId = Number(tab && tab.id);
      if (!Number.isInteger(tabId) || tabId <= 0) return { ok: false, retry: false };
      let hostname = '';
      try { hostname = new URL(String(tab.url || '')).hostname; } catch (_error) {}
      if (!['chatgpt.com', 'chat.openai.com'].includes(hostname)) {
        return { ok: false, retry: false };
      }
      const reloading = await safeRefreshTab(tabId, String(version || ''));
      return { ok: reloading, reloading, retry: false };
    }

    async function reconnectTab(tab) {
      const tabId = Number(tab && tab.id);
      if (!Number.isInteger(tabId) || tabId <= 0) return false;
      if (tab.status === 'loading' || tab.discarded === true) return false;
      if (ctx.reconnectingTabs.has(tabId)) return ctx.reconnectingTabs.get(tabId);
      const attempt = (async () => {
        try {
          const response = await settleWithin(ctx.chrome.tabs.sendMessage(tabId, { type: 'bridge-reconnect' }));
          if (hasCurrentContentScript(response)) {
            pendingContentUpdateTabs.delete(tabId);
            return true;
          }
          if (response && response.ok === true && contentScriptVersion) {
            pendingContentUpdateTabs.add(tabId);
            await ensureUpdateWatcher(tabId);
            return false;
          }
          if (response) return false;
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
          if (hasCurrentContentScript(response)) {
            pendingContentUpdateTabs.delete(tabId);
            return true;
          }
          if (response && response.ok === true && contentScriptVersion) {
            pendingContentUpdateTabs.add(tabId);
            await ensureUpdateWatcher(tabId);
          }
          return false;
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
      for (const tabId of Array.from(pendingContentUpdateTabs)) {
        if (!openTabIds.has(tabId)) pendingContentUpdateTabs.delete(tabId);
      }
      for (const tabId of Array.from(ctx.tabs.keys())) {
        if (!openTabIds.has(tabId)) ctx.tabs.delete(tabId);
      }
      ctx.ensureOwner();
      await Promise.all(openTabs.map(reconnectTab));
      ctx.broadcastState();
      return true;
    }

    return {
      reconnectTab,
      reconnectOpenTabs,
      handlePageReadyForUpdate,
      forgetTab(tabId) {
        pendingContentUpdateTabs.delete(Number(tabId));
        managedRefreshTabs.delete(Number(tabId));
        if (ctx.persistManagedRefreshTabs) void ctx.persistManagedRefreshTabs().catch(() => {});
      },
      completeManagedRefresh(tabId) {
        managedRefreshTabs.delete(Number(tabId));
        if (ctx.persistManagedRefreshTabs) return ctx.persistManagedRefreshTabs();
        return Promise.resolve();
      },
      pendingContentUpdateCount: () => pendingContentUpdateTabs.size,
    };
  }

  global.BackgroundTabReconnect = Object.freeze({ CONTENT_SCRIPT_FILES, create });
})(globalThis);
