'use strict';

(function exposeBackgroundRuntimeCore(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundRuntimeCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  function cloneItem(item) {
    return item ? { ...item } : null;
  }

  function createPayload(state) {
    const safe = state && typeof state === 'object' ? state : {};
    const tabs = safe.tabs instanceof Map ? safe.tabs : new Map();
    const sessionTargets = safe.conversationSessionTargets instanceof Map
      ? safe.conversationSessionTargets
      : new Map();
    const sessionLocations = safe.conversationSessionTargetLocations instanceof Map
      ? safe.conversationSessionTargetLocations
      : new Map();
    return {
      tabs: Array.from(tabs.entries()).map(([id, info]) => ({
        id,
        title: String(info?.title || 'ChatGPT'),
        url: String(info?.url || ''),
        lastReadIndex: Number.isInteger(info?.lastReadIndex) ? info.lastReadIndex : -1,
        lastAutoQueueSignature: String(info?.lastAutoQueueSignature || ''),
        lastAssistantMessage: info?.lastAssistantMessage ? {
          messageKey: String(info.lastAssistantMessage.messageKey || ''),
          chunks: Array.isArray(info.lastAssistantMessage.chunks)
            ? info.lastAssistantMessage.chunks.map((chunk) => String(chunk || ''))
            : [],
          capturedAt: Number(info.lastAssistantMessage.capturedAt || 0),
        } : null,
      })),
      selectedTabId: Number(safe.selectedTabId || 0),
      uiOwnerTabId: Number(safe.uiOwnerTabId || 0),
      lastComposerFocusedTabId: Number(safe.lastComposerFocusedTabId || 0),
      activeConversationTargetTabId: Number(safe.activeConversationTargetTabId || 0),
      conversationSessions: Array.from(sessionTargets.entries()).map(([sessionId, tabId]) => ({
        sessionId: Number(sessionId || 0),
        tabId: Number(tabId || 0),
        location: String(sessionLocations.get(sessionId) || ''),
      })).filter((item) => item.sessionId > 0),
      queue: Array.isArray(safe.queue) ? safe.queue.map(cloneItem) : [],
      currentItem: cloneItem(safe.currentItem),
      lastPlayedItem: cloneItem(safe.lastPlayedItem),
      seq: Number(safe.seq || 1),
    };
  }

  function normalizeTab(item) {
    const tabId = Number(item?.id || 0);
    if (!Number.isInteger(tabId) || tabId <= 0) return null;
    const message = item?.lastAssistantMessage && typeof item.lastAssistantMessage === 'object'
      ? {
        messageKey: String(item.lastAssistantMessage.messageKey || ''),
        chunks: Array.isArray(item.lastAssistantMessage.chunks)
          ? item.lastAssistantMessage.chunks.map((chunk) => String(chunk || '').trim()).filter(Boolean)
          : [],
        capturedAt: Number(item.lastAssistantMessage.capturedAt || 0),
      }
      : null;
    return [tabId, {
      title: String(item?.title || 'ChatGPT'),
      url: String(item?.url || ''),
      lastReadIndex: Number.isInteger(Number(item?.lastReadIndex)) ? Number(item.lastReadIndex) : -1,
      lastAutoQueueSignature: String(item?.lastAutoQueueSignature || ''),
      lastAssistantMessage: message && message.messageKey && message.chunks.length ? message : null,
    }];
  }

  function queueIdentity(item) {
    return [item?.id, item?.mode, item?.tabId, item?.messageKey, item?.chunkIndex, item?.text].join('\u0000');
  }

  function mergeSnapshot(value, liveState) {
    const raw = value && typeof value === 'object' ? value : {};
    const live = liveState && typeof liveState === 'object' ? liveState : {};
    const mergedTabs = new Map();
    for (const item of Array.isArray(raw.tabs) ? raw.tabs : []) {
      const normalized = normalizeTab(item);
      if (normalized) mergedTabs.set(normalized[0], normalized[1]);
    }
    if (live.tabs instanceof Map) {
      for (const [tabId, info] of live.tabs.entries()) mergedTabs.set(tabId, info);
    }

    const mergedSessionTargets = new Map();
    const mergedSessionLocations = new Map();
    for (const item of Array.isArray(raw.conversationSessions) ? raw.conversationSessions : []) {
      const sessionId = Number(item?.sessionId || 0);
      if (!Number.isInteger(sessionId) || sessionId <= 0) continue;
      mergedSessionTargets.set(sessionId, Number(item.tabId || 0));
      mergedSessionLocations.set(sessionId, String(item.location || ''));
    }
    if (live.conversationSessionTargets instanceof Map) {
      for (const [sessionId, tabId] of live.conversationSessionTargets.entries()) {
        mergedSessionTargets.set(sessionId, tabId);
        mergedSessionLocations.set(
          sessionId,
          String(live.conversationSessionTargetLocations?.get(sessionId) || ''),
        );
      }
    }

    const hadLivePlayback = Boolean(live.isPlaying || live.currentItem);
    const persistedCurrentItem = !hadLivePlayback && raw.currentItem ? cloneItem(raw.currentItem) : null;
    const restoredQueue = [
      ...(persistedCurrentItem ? [persistedCurrentItem] : []),
      ...(Array.isArray(raw.queue) ? raw.queue : []),
      ...(Array.isArray(live.queue) ? live.queue : []),
    ].map(cloneItem).filter((item) => item && String(item.text || '').trim());
    const seen = new Set();
    const queue = restoredQueue.filter((item) => {
      const key = queueIdentity(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    return {
      tabs: mergedTabs,
      conversationSessionTargets: mergedSessionTargets,
      conversationSessionTargetLocations: mergedSessionLocations,
      selectedTabId: live.selectedTabId || Number(raw.selectedTabId || 0) || null,
      uiOwnerTabId: live.uiOwnerTabId || Number(raw.uiOwnerTabId || 0) || null,
      lastComposerFocusedTabId: live.lastComposerFocusedTabId
        || Number(raw.lastComposerFocusedTabId || 0)
        || null,
      activeConversationTargetTabId: live.activeConversationTargetTabId
        || Number(raw.activeConversationTargetTabId || 0)
        || null,
      queue,
      resetPlayback: !hadLivePlayback,
      lastPlayedItem: cloneItem(live.lastPlayedItem) || cloneItem(raw.lastPlayedItem),
      seq: Math.max(1, Number(raw.seq || 1), Number(live.seq || 1)),
    };
  }

  return { cloneItem, createPayload, mergeSnapshot, queueIdentity };
});
