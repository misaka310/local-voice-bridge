'use strict';

(function initBackgroundRuntimeStore(global) {
  function create(ctx) {
    let hydrated = false;
    let hydrationPromise = null;
    let persistTimer = null;
    let persistTail = Promise.resolve();
    let persistDirty = false;

    function cloneItem(item) {
      return ctx.runtimeCore.cloneItem(item);
    }

    function browserRuntimeStatePayload() {
      return ctx.runtimeCore.createPayload(ctx.snapshot());
    }

    function applyBrowserRuntimeSnapshot(value) {
      const merged = ctx.runtimeCore.mergeSnapshot(value, ctx.snapshot());
      ctx.applyMerged(merged);
      ctx.ensureOwner();
      hydrated = true;
      scheduleBrowserRuntimePersist();
      return true;
    }

    async function hydrateBrowserRuntime() {
      if (hydrated) return true;
      if (hydrationPromise) return hydrationPromise;
      hydrationPromise = (async () => {
        try {
          const settings = await ctx.getSettings();
          const payload = await ctx.controlPanelRequest(settings, '/v1/browser-runtime');
          if (payload.browserRuntime && payload.browserRuntime.currentItem) {
            await ctx.stopLocalAudio().catch(() => {});
          }
          applyBrowserRuntimeSnapshot(payload.browserRuntime);
          if (ctx.queueLength() && !ctx.isPlaying()) void ctx.playNext();
          return true;
        } catch (_error) {
          hydrated = false;
          return false;
        }
      })();
      try {
        return await hydrationPromise;
      } finally {
        hydrationPromise = null;
      }
    }

    async function flushBrowserRuntimeState() {
      if (!hydrated) return;
      if (persistTimer) {
        clearTimeout(persistTimer);
        persistTimer = null;
      }
      persistDirty = false;
      const body = browserRuntimeStatePayload();
      const operation = persistTail.catch(() => {}).then(async () => {
        const settings = await ctx.getSettings();
        await ctx.controlPanelRequest(settings, '/v1/browser-runtime', {
          method: 'POST',
          body,
        });
      });
      persistTail = operation;
      try {
        await operation;
      } finally {
        if (persistTail === operation) persistTail = Promise.resolve();
        if (persistDirty) scheduleBrowserRuntimePersist();
      }
    }

    function scheduleBrowserRuntimePersist() {
      if (!hydrated) return;
      persistDirty = true;
      if (persistTimer) return;
      persistTimer = setTimeout(() => {
        persistTimer = null;
        void flushBrowserRuntimeState().catch(() => {});
      }, 25);
      if (persistTimer && typeof persistTimer.unref === 'function') persistTimer.unref();
    }

    return {
      cloneItem,
      browserRuntimeStatePayload,
      applyBrowserRuntimeSnapshot,
      hydrateBrowserRuntime,
      flushBrowserRuntimeState,
      scheduleBrowserRuntimePersist,
    };
  }

  global.BackgroundRuntimeStore = Object.freeze({ create });
})(globalThis);
