'use strict';

(function initBackgroundMessageRouter(global) {
  function create(ctx) {
    return (message, sender, sendResponse) => {
      if (!message || typeof message.type !== 'string') return false;
      const senderTabId = sender.tab ? sender.tab.id : null;

      if (message.type === 'tab-attention-state') {
        if (!senderTabId) {
          sendResponse({ ok: true, payload: { active: false } });
          return false;
        }
        ctx.chrome.tabs.get(senderTabId)
          .then((tab) => sendResponse({ ok: true, payload: { active: Boolean(tab && tab.active) } }))
          .catch(() => sendResponse({ ok: true, payload: { active: false } }));
        return true;
      }

      if (message.type === 'register-tab') {
        ctx.registerTab(message, sender);
        sendResponse({ ok: true, payload: ctx.statePayload(senderTabId) });
        ctx.broadcastState();
        return false;
      }

      if (message.type === 'composer-focused') {
        if (ctx.noteComposerFocused(senderTabId)) {
          sendResponse({ ok: true, payload: { targetTabId: senderTabId } });
        } else {
          sendResponse({ ok: false, reason: 'tab-not-registered' });
        }
        return false;
      }

      if (message.type === 'report-chunks') {
        if (senderTabId && ctx.tabs.has(senderTabId)) {
          const report = ctx.queueCore.applyAssistantReport(ctx.tabs.get(senderTabId), message, {
            tabId: senderTabId,
            allowAuto: ctx.shouldQueueAutoFromTab(senderTabId),
            capturedAt: Date.now(),
          });
          if (report.changed) ctx.tabs.set(senderTabId, report.info);
          if (report.enqueueBase) {
            ctx.enqueue(report.enqueueBase);
            void ctx.playNext();
          } else if (report.suppressedAuto) {
            ctx.setStatus('音声入力中のため別の返答は読み上げませんでした', 'info');
          }
        }
        sendResponse({ ok: true, payload: ctx.statusPayload() });
        ctx.broadcastState();
        return false;
      }

      if (message.type === 'playback-started') {
        const token = String(message.playbackToken || '');
        if (ctx.isPlaying() && token === ctx.currentToken() && senderTabId === ctx.currentPlaybackTabId()) {
          ctx.armPlaybackWatchdog(ctx.playbackLeaseMs(message.durationSeconds));
          sendResponse({ ok: true, payload: { accepted: true } });
        } else {
          sendResponse({ ok: true, payload: { ignored: true } });
        }
        return false;
      }

      if (message.type === 'playback-done') {
        sendResponse(ctx.finishPlayback(message));
        return false;
      }

      if (message.type === 'fetch-audio') {
        ctx.fetchAudioPayload(message.url)
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'reference-voices') {
        ctx.fetchReferenceVoices()
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'desktop-pet-selection') {
        ctx.syncDesktopPetSelection(message.petId)
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'options-settings-updated') {
        ctx.getSettings()
          .then((settings) => ctx.pushOptionSettings(settings))
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'external-control-poll') {
        ctx.syncExternalControlPanel()
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (ctx.liveMessageTypes.has(message.type)) {
        ctx.liveClient.handle(message, senderTabId, Boolean(senderTabId && ctx.tabs.has(senderTabId)))
          .then((result) => sendResponse(result.response || { ok: false, error: 'live-message-unhandled' }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'conversation-state') {
        ctx.postConversationState(message.payload)
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }

      if (message.type === 'ui-command') {
        const params = message.params && typeof message.params === 'object' ? message.params : {};
        const result = ctx.executeUiCommand(message.cmd, senderTabId, params);
        ctx.flushBrowserRuntimeState()
          .then(() => sendResponse(result))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }
      return false;
    };
  }

  global.BackgroundMessageRouter = Object.freeze({ create });
})(globalThis);
