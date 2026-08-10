'use strict';

(function initBackgroundTabRegistry(global) {
  function create(ctx) {
    function ensureOwner() {
      const ids = Array.from(ctx.tabs.keys());
      if (!ids.length) {
        ctx.setUiOwnerTabId(null);
        ctx.setSelectedTabId(null);
        ctx.setLastComposerFocusedTabId(null);
        ctx.setActiveConversationTargetTabId(null);
        ctx.conversationSessionTargets.clear();
        ctx.conversationSessionTargetLocations.clear();
        return;
      }
      if (!ctx.uiOwnerTabId() || !ctx.tabs.has(ctx.uiOwnerTabId())) ctx.setUiOwnerTabId(ids[0]);
      if (!ctx.selectedTabId() || !ctx.tabs.has(ctx.selectedTabId())) ctx.setSelectedTabId(ctx.uiOwnerTabId());
      if (ctx.lastComposerFocusedTabId() && !ctx.tabs.has(ctx.lastComposerFocusedTabId())) {
        ctx.setLastComposerFocusedTabId(null);
      }
      if (ctx.activeConversationTargetTabId() && !ctx.tabs.has(ctx.activeConversationTargetTabId())) {
        ctx.setActiveConversationTargetTabId(null);
      }
    }

    function registerTab(message, sender) {
      const senderTabId = sender && sender.tab ? sender.tab.id : null;
      if (!senderTabId) return null;
      const wasRegistered = ctx.tabs.has(senderTabId);
      const existing = ctx.tabs.get(senderTabId)
        || { lastAssistantMessage: null, lastReadIndex: -1, lastAutoQueueSignature: '' };
      const previousTitle = String(existing.title || '');
      const previousUrl = String(existing.url || '');
      const previousOwner = ctx.uiOwnerTabId();
      const previousSelected = ctx.selectedTabId();
      existing.title = String(message.title || sender.tab.title || 'ChatGPT');
      existing.url = sender.tab.url || existing.url || '';
      ctx.tabs.set(senderTabId, existing);
      if (message.claimOwner === true || (ctx.uiOwnerTabId() == null && sender.tab.active)) {
        ctx.setUiOwnerTabId(senderTabId);
        ctx.setSelectedTabId(senderTabId);
      }
      return {
        tabId: senderTabId,
        changed: !wasRegistered
          || previousTitle !== existing.title
          || previousUrl !== existing.url
          || previousOwner !== ctx.uiOwnerTabId()
          || previousSelected !== ctx.selectedTabId(),
      };
    }

    function noteComposerFocused(tabId) {
      if (!tabId || !ctx.tabs.has(tabId)) return false;
      ctx.setLastComposerFocusedTabId(tabId);
      return true;
    }

    function removeConversationTargets(tabId) {
      if (ctx.lastComposerFocusedTabId() === tabId) ctx.setLastComposerFocusedTabId(null);
      if (ctx.activeConversationTargetTabId() === tabId) ctx.setActiveConversationTargetTabId(null);
      for (const [sessionId, targetTabId] of ctx.conversationSessionTargets.entries()) {
        if (targetTabId === tabId) ctx.conversationSessionTargets.set(sessionId, 0);
      }
    }

    function removeTab(tabId, reason) {
      const ownedCurrentPlayback = ctx.isPlaying() && ctx.currentPlaybackTabId() === tabId;
      ctx.tabs.delete(tabId);
      ctx.setQueue(ctx.queue().filter((item) => item.tabId !== tabId));
      if (ctx.uiOwnerTabId() === tabId) ctx.setUiOwnerTabId(null);
      if (ctx.selectedTabId() === tabId) ctx.setSelectedTabId(null);
      removeConversationTargets(tabId);
      if (ownedCurrentPlayback) ctx.abandonCurrentPlayback(reason, 'warn');
      else ctx.broadcastState();
    }

    function activateTab(tabId) {
      if (!ctx.tabs.has(tabId)) return false;
      ctx.setUiOwnerTabId(tabId);
      ctx.setSelectedTabId(tabId);
      ctx.chrome.tabs.sendMessage(tabId, { type: 'tab-activated' }).catch(() => {});
      ctx.broadcastState();
      return true;
    }

    return {
      ensureOwner,
      registerTab,
      noteComposerFocused,
      removeTab,
      activateTab,
    };
  }

  global.BackgroundTabRegistry = Object.freeze({ create });
})(globalThis);
