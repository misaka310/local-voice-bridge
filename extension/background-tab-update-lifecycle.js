'use strict';

(function exposeBackgroundTabUpdateLifecycle(global) {
  const STORAGE_KEY = 'managedChatGptRefreshTabs';

  function isChatGptUrl(value) {
    try {
      return ['chatgpt.com', 'chat.openai.com'].includes(new URL(String(value || '')).hostname);
    } catch (_error) {
      return false;
    }
  }

  function createState(chrome, managedRefreshTabs = new Set()) {
    async function persist() {
      if (!chrome.storage.session || typeof chrome.storage.session.set !== 'function') return;
      await chrome.storage.session.set({ [STORAGE_KEY]: Array.from(managedRefreshTabs) });
    }

    async function has(tabId) {
      const id = Number(tabId);
      if (managedRefreshTabs.has(id)) return true;
      if (!chrome.storage.session || typeof chrome.storage.session.get !== 'function') return false;
      try {
        const stored = await chrome.storage.session.get(STORAGE_KEY);
        const ids = Array.isArray(stored && stored[STORAGE_KEY])
          ? stored[STORAGE_KEY].map(Number)
          : [];
        if (!ids.includes(id)) return false;
        managedRefreshTabs.add(id);
        return true;
      } catch (_error) {
        return false;
      }
    }

    return { persist, has, tabs: managedRefreshTabs };
  }

  function createEvents(ctx) {
    async function onRemoved(tabId) {
      ctx.clearAutoRecheck(tabId);
      ctx.tabReconnect.forgetTab(tabId);
      ctx.removeTab(tabId, 'Playback tab closed; skipped');
    }

    function onUpdated(tabId, changeInfo, tab) {
      if (!changeInfo) return;
      if (changeInfo.status === 'loading') {
        ctx.clearAutoRecheck(tabId);
        void ctx.state.has(tabId).then((managed) => {
          if (!managed) ctx.removeTab(tabId, 'Playback tab reloaded; skipped');
        });
        return;
      }
      if (changeInfo.status !== 'complete') return;
      void ctx.state.has(tabId).then(async (managed) => {
        if (!managed) return;
        await ctx.tabReconnect.completeManagedRefresh(tabId);
        if (isChatGptUrl(tab && tab.url)) void ctx.tabReconnect.reconnectTab(tab);
        else ctx.removeTab(tabId, 'Playback tab navigated away; skipped');
      });
    }

    return { onRemoved, onUpdated };
  }

  global.BackgroundTabUpdateLifecycle = Object.freeze({ STORAGE_KEY, createState, createEvents, isChatGptUrl });
})(globalThis);
