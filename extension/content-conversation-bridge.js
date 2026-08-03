'use strict';

(function initContentConversationBridge(global) {
  function create(ctx) {
    let currentPhase = 'off';
    let pendingSendController = null;
    let cancelOverlay = null;
    let deliveryLedger = null;

    function hideCancelOverlay() {
      if (cancelOverlay) cancelOverlay.remove();
      cancelOverlay = null;
    }

    function showCancelOverlay(graceMs) {
      hideCancelOverlay();
      cancelOverlay = ctx.document.createElement('div');
      cancelOverlay.id = 'local-voice-cancel-hint';
      cancelOverlay.textContent = `Escでキャンセル · ${(Math.max(0, Number(graceMs) || 0) / 1000).toFixed(1)}秒`;
      Object.assign(cancelOverlay.style, {
        position: 'fixed',
        right: '18px',
        bottom: '18px',
        zIndex: '2147483647',
        padding: '7px 10px',
        borderRadius: '8px',
        background: 'rgba(16, 18, 24, 0.88)',
        color: '#f7f8fb',
        font: '12px system-ui, sans-serif',
        pointerEvents: 'none',
        boxShadow: '0 4px 18px rgba(0, 0, 0, 0.25)',
      });
      ctx.document.documentElement.appendChild(cancelOverlay);
    }

    function reportConversationState(payload) {
      currentPhase = String(payload && payload.phase || 'error');
      if (!ctx.chrome || !ctx.chrome.runtime || !ctx.chrome.runtime.sendMessage) return;
      ctx.chrome.runtime.sendMessage({ type: 'conversation-state', payload }).catch(() => {});
    }

    function reportComposerFocus(target = ctx.document.activeElement) {
      const api = global.LocalVoicePromptInput;
      if (!api || typeof api.findComposer !== 'function' || !target) return;
      const composer = api.findComposer(ctx.document, target);
      if (!composer) return;
      const isTarget = typeof api.isComposerTarget === 'function'
        ? api.isComposerTarget(ctx.document, target)
        : target === composer || (typeof composer.contains === 'function' && composer.contains(target));
      if (!isTarget) return;
      ctx.chrome.runtime.sendMessage({
        type: 'composer-focused',
        title: ctx.getPlainDocumentTitle(),
      }).catch(() => {});
    }

    function conversationTargetStatus() {
      const api = global.LocalVoicePromptInput;
      if (!api || typeof api.findComposer !== 'function') {
        return { ok: false, reason: 'prompt-input-core-unavailable' };
      }
      const activeElement = ctx.document.activeElement || null;
      const composer = api.findComposer(ctx.document, activeElement);
      const composerFocused = Boolean(composer && activeElement && (
        typeof api.isComposerTarget === 'function'
          ? api.isComposerTarget(ctx.document, activeElement)
          : activeElement === composer || (typeof composer.contains === 'function' && composer.contains(activeElement))
      ));
      const documentFocused = typeof ctx.document.hasFocus === 'function' ? ctx.document.hasFocus() : true;
      const visible = ctx.document.visibilityState !== 'hidden';
      return {
        ok: true,
        composerAvailable: Boolean(composer),
        composerFocused: Boolean(composerFocused && documentFocused && visible),
        documentFocused: Boolean(documentFocused),
        visible,
        url: ctx.location.href,
      };
    }

    function appliedDeliveryLedger() {
      if (deliveryLedger) return deliveryLedger;
      const api = global.LocalVoiceDeliveryIds;
      if (!api || typeof api.createLedger !== 'function') return null;
      deliveryLedger = api.createLedger(ctx.sessionStorage, {
        key: 'localVoiceAppliedDeliveryIds',
        limit: 128,
      });
      return deliveryLedger;
    }

    function ensurePendingSendController() {
      if (pendingSendController) return pendingSendController;
      const api = global.LocalVoicePromptInput;
      if (!api || typeof api.createPendingSendController !== 'function') return null;
      const live = ctx.ensureLiveController();
      if (!live) return null;
      pendingSendController = api.createPendingSendController({
        document: ctx.document,
        window: ctx.window,
        Event: ctx.Event,
        InputEvent: ctx.InputEvent,
        getLocation: () => ctx.location.href,
        prepareSubmission: (item) => live.prepareSubmission(item),
        commitSubmission: (item) => live.commitSubmission(item),
        invalidateSubmission: (item, reason) => live.invalidateSubmission(item, reason),
        markSubmissionClick: (item, composer) => live.markSubmissionClick(item, composer),
        onState: (state) => {
          const settings = ctx.getSettings();
          if (state.phase === 'pending_send') showCancelOverlay(settings.cancelGraceMs);
          else hideCancelOverlay();
          reportConversationState({
            phase: state.phase,
            statusText: state.statusText,
            error: state.error || '',
            sttModel: settings.sttModel || 'small',
          });
        },
      });
      return pendingSendController;
    }

    function handleVoiceTranscript(message, sendResponse) {
      const settings = ctx.getSettings();
      if (!settings.micConversationEnabled) {
        sendResponse({ ok: false, reason: 'mic-conversation-disabled' });
        return false;
      }
      const controller = ensurePendingSendController();
      if (!controller) {
        reportConversationState({
          phase: 'error',
          statusText: '音声入力モジュールを読み込めませんでした',
          error: 'prompt-input-core-unavailable',
          sttModel: settings.sttModel,
        });
        sendResponse({ ok: false, reason: 'prompt-input-core-unavailable' });
        return false;
      }
      const payload = message.payload || {};
      const deliveryId = String(payload.deliveryId || '').trim();
      const ledger = appliedDeliveryLedger();
      if (deliveryId && ledger && ledger.has(deliveryId)) {
        sendResponse({ ok: true, alreadyApplied: true });
        return false;
      }
      const graceMs = Math.max(0, Math.min(5000, Number(payload.cancelGraceMs) || 0));
      ctx.updateCancelGraceMs(graceMs);
      const live = ctx.ensureLiveController();
      if (!live) {
        sendResponse({ ok: false, reason: 'live-content-controller-unavailable' });
        return false;
      }
      const text = String(payload.text || '');
      const metadata = live.metadata(String(payload.sessionId || ''), text);
      Promise.resolve(controller.start({
        ...metadata,
        text,
        graceMs,
      })).then((result) => {
        if (result && result.ok === true && deliveryId && ledger) ledger.mark(deliveryId);
        sendResponse(result);
      }).catch((error) => sendResponse({ ok: false, reason: error.message || String(error) }));
      return true;
    }

    function cancelPending(reason = 'new-recording') {
      const controller = ensurePendingSendController();
      const result = controller ? controller.cancel(reason) : { ok: false, reason: 'nothing-pending' };
      hideCancelOverlay();
      return result;
    }

    function disable() {
      if (pendingSendController) pendingSendController.cancel('disabled');
      void ctx.ensureLiveController()?.interrupt('disabled');
      hideCancelOverlay();
      const settings = ctx.getSettings();
      reportConversationState({
        phase: 'off',
        statusText: 'マイク会話オフ',
        error: '',
        sttModel: settings.sttModel,
      });
    }

    function handlePageHide() {
      if (pendingSendController) pendingSendController.cancel('page-changed');
      void ctx.ensureLiveController()?.interrupt('pagehide');
      hideCancelOverlay();
    }

    return {
      getPhase: () => currentPhase,
      reportConversationState,
      reportComposerFocus,
      conversationTargetStatus,
      handleVoiceTranscript,
      cancelPending,
      disable,
      handlePageHide,
    };
  }

  global.LocalVoiceContentConversationBridge = Object.freeze({ create });
})(globalThis);
