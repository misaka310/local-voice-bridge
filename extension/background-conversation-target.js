'use strict';

(function initBackgroundConversationTarget(global) {
  function create(ctx) {
    function preferredConversationTarget() {
      ctx.ensureOwner();
      if (ctx.lastComposerFocusedTabId() && ctx.tabs.has(ctx.lastComposerFocusedTabId())) {
        return ctx.lastComposerFocusedTabId();
      }
      if (ctx.selectedTabId() && ctx.tabs.has(ctx.selectedTabId())) return ctx.selectedTabId();
      return ctx.uiOwnerTabId() && ctx.tabs.has(ctx.uiOwnerTabId()) ? ctx.uiOwnerTabId() : null;
    }

    async function conversationTargetStatus(tabId) {
      try {
        const response = await ctx.chrome.tabs.sendMessage(tabId, { type: 'conversation-target-status' });
        if (!response || response.ok !== true) {
          return { tabId, ok: false, composerAvailable: false, composerFocused: false };
        }
        return {
          tabId,
          ok: true,
          composerAvailable: Boolean(response.composerAvailable),
          composerFocused: Boolean(response.composerFocused),
          documentFocused: Boolean(response.documentFocused),
          visible: Boolean(response.visible),
          url: String(response.url || ''),
        };
      } catch (_error) {
        return { tabId, ok: false, composerAvailable: false, composerFocused: false };
      }
    }

    function preferredStatus(statuses, priorities) {
      return priorities
        .map((tabId) => statuses.find((status) => status.tabId === tabId))
        .find(Boolean) || statuses[0];
    }

    async function captureConversationTarget() {
      ctx.ensureOwner();
      const tabIds = Array.from(ctx.tabs.keys());
      if (!tabIds.length) return null;
      const statuses = await Promise.all(tabIds.map((tabId) => conversationTargetStatus(tabId)));
      const byTabId = new Map(statuses.map((status) => [status.tabId, status]));
      const focused = statuses.filter((status) => status.ok && status.composerFocused);
      if (focused.length) {
        return preferredStatus(focused, [
          ctx.lastComposerFocusedTabId(),
          ctx.selectedTabId(),
          ctx.uiOwnerTabId(),
        ]);
      }
      const focusedDocuments = statuses.filter(
        (status) => status.ok && status.composerAvailable && status.documentFocused,
      );
      if (focusedDocuments.length) {
        return preferredStatus(focusedDocuments, [
          ctx.selectedTabId(),
          ctx.uiOwnerTabId(),
          ctx.lastComposerFocusedTabId(),
        ]);
      }
      const visibleDocuments = statuses.filter(
        (status) => status.ok && status.composerAvailable && status.visible,
      );
      if (visibleDocuments.length) {
        return preferredStatus(visibleDocuments, [
          ctx.selectedTabId(),
          ctx.uiOwnerTabId(),
          ctx.lastComposerFocusedTabId(),
        ]);
      }
      const candidates = [
        ctx.lastComposerFocusedTabId(),
        ctx.selectedTabId(),
        ctx.uiOwnerTabId(),
        ...tabIds,
      ];
      for (const tabId of candidates) {
        const status = byTabId.get(tabId);
        if (status && status.ok && status.composerAvailable) return status;
      }
      return null;
    }

    function conversationLocationKey(value) {
      const raw = String(value || '').trim();
      if (!raw) return '';
      try {
        const url = new URL(raw);
        return `${url.origin}${url.pathname}`;
      } catch (_error) {
        return raw.split(/[?#]/, 1)[0];
      }
    }

    function retryableTranscriptFailure(reason) {
      return [
        'composer-not-found',
        'composer-state-not-updated',
        'prompt-input-core-unavailable',
        'message-delivery-failed',
      ].includes(String(reason || ''));
    }

    function delay(milliseconds) {
      return new Promise((resolve) => setTimeout(resolve, milliseconds));
    }

    async function deliverVoiceTranscript(targetTabId, payload, settings) {
      let lastReason = 'message-delivery-failed';
      for (let attempt = 0; attempt < 3; attempt += 1) {
        try {
          const response = await ctx.chrome.tabs.sendMessage(targetTabId, { type: 'voice-transcript', payload });
          if (response && response.ok === true) {
            return { ok: true, alreadyApplied: Boolean(response.alreadyApplied) };
          }
          lastReason = String(response && (response.reason || response.error) || 'message-delivery-failed');
        } catch (_error) {
          lastReason = 'message-delivery-failed';
        }
        if (!retryableTranscriptFailure(lastReason) || attempt === 2) break;
        await delay(50 * (attempt + 1));
      }
      await ctx.postConversationState({
        phase: 'error',
        statusText: '音声入力をChatGPT入力欄へ反映できませんでした',
        sttModel: settings.sttModel || 'small',
        error: lastReason,
      }).catch(() => {});
      return { ok: false, reason: lastReason, retryable: retryableTranscriptFailure(lastReason) };
    }

    function shouldQueueAutoFromTab(_tabId) {
      return ctx.queueCore.shouldQueueAuto(ctx.conversationPhase());
    }

    return {
      preferredConversationTarget,
      conversationTargetStatus,
      captureConversationTarget,
      conversationLocationKey,
      deliverVoiceTranscript,
      shouldQueueAutoFromTab,
    };
  }

  global.BackgroundConversationTarget = Object.freeze({ create });
})(globalThis);
