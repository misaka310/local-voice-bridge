'use strict';

(function exposeAutoSpeechController(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceAutoSpeech = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  function createAutoSpeechController(environment = {}) {
    const getAssistantNodes = environment.getAssistantNodes;
    const extractAssistantText = environment.extractAssistantText;
    const getStableKey = environment.getStableKey;
    const isResponseGenerating = environment.isResponseGenerating;
    const hasResponseCompletionControl = environment.hasResponseCompletionControl;
    const getPreviewOptions = environment.getPreviewOptions;
    const splitSpeakChunks = environment.splitSpeakChunks;
    const extractAutoPreview = environment.extractAutoPreview;
    const stableDelayForPreview = environment.stableDelayForPreview;
    const canFinalizePreview = environment.canFinalizePreview;
    const reportChunks = environment.reportChunks;
    const markResponseCompleted = environment.markResponseCompleted;
    const isAutoEnabled = environment.isAutoEnabled;
    const isGenerationControlNode = environment.isGenerationControlNode;
    const afterInspectLatest = typeof environment.afterInspectLatest === 'function'
      ? environment.afterInspectLatest
      : () => {};
    const now = typeof environment.now === 'function' ? environment.now : Date.now;
    const setTimer = environment.setTimeout || globalThis.setTimeout.bind(globalThis);
    const clearTimer = environment.clearTimeout || globalThis.clearTimeout.bind(globalThis);
    const sentFlag = String(environment.sentFlag || 'localVoiceSent');
    const inspectDelayMs = Math.max(0, Number(environment.inspectDelayMs || 200));
    const stateByElement = new WeakMap();
    const initializedElements = new WeakSet();
    let inspectTimer = null;

    for (const [name, value] of Object.entries({
      getAssistantNodes,
      extractAssistantText,
      getStableKey,
      isResponseGenerating,
      hasResponseCompletionControl,
      getPreviewOptions,
      splitSpeakChunks,
      extractAutoPreview,
      stableDelayForPreview,
      canFinalizePreview,
      reportChunks,
      markResponseCompleted,
      isAutoEnabled,
      isGenerationControlNode,
    })) {
      if (typeof value !== 'function') throw new Error(`auto speech dependency missing: ${name}`);
    }

    function createState(node, text, overrides = {}) {
      return {
        key: getStableKey(node),
        sent: false,
        completionNotified: false,
        generationObserved: false,
        generationCompleted: false,
        completionControlObserved: false,
        lastText: text,
        lastChangedAt: now(),
        idleTimer: null,
        completionTimer: null,
        ...overrides,
      };
    }

    function clearStateTimers(item) {
      if (!item) return;
      if (item.idleTimer) clearTimer(item.idleTimer);
      if (item.completionTimer) clearTimer(item.completionTimer);
      item.idleTimer = null;
      item.completionTimer = null;
    }

    function ensureElementState(node, text) {
      let item = stateByElement.get(node);
      if (!item) {
        const alreadySent = Boolean(node && node.dataset && node.dataset[sentFlag] === '1');
        item = createState(node, text, {
          sent: alreadySent,
          completionNotified: alreadySent,
        });
        stateByElement.set(node, item);
      }
      return item;
    }

    function previewParts(text) {
      const options = getPreviewOptions();
      const chunks = splitSpeakChunks(text, options);
      const preview = extractAutoPreview(text, options);
      return { chunks, preview };
    }

    function observeCompletion(node, item) {
      const generating = Boolean(isResponseGenerating());
      if (generating) item.generationObserved = true;
      else if (item.generationObserved) item.generationCompleted = true;
      if (hasResponseCompletionControl(node)) item.completionControlObserved = true;
      return {
        generating,
        confirmed: Boolean(item.generationCompleted || item.completionControlObserved),
      };
    }

    function shouldSendNow(node, preview, timestamp, item) {
      if (!preview) return false;
      const options = getPreviewOptions();
      const minChars = Math.max(1, Number(options.minChars || 40));
      const completion = observeCompletion(node, item);
      if (!canFinalizePreview(preview, {
        minChars,
        completionConfirmed: completion.confirmed,
      })) return false;
      if (completion.generating && preview.length < minChars) return false;
      return timestamp - item.lastChangedAt >= stableDelayForPreview(preview);
    }

    function maybeMarkCompleted(node, item, text) {
      if (!item.sent || item.completionNotified) return;
      if (isResponseGenerating()) {
        item.generationObserved = true;
        if (item.completionTimer) clearTimer(item.completionTimer);
        item.completionTimer = null;
        return;
      }
      const { preview } = previewParts(text);
      if (!preview) return;
      const stableMs = stableDelayForPreview(preview);
      const requiredStableMs = item.generationObserved ? 0 : Math.max(stableMs, 1800);
      const remainingMs = requiredStableMs - (now() - item.lastChangedAt);
      if (remainingMs > 0) {
        if (item.completionTimer) clearTimer(item.completionTimer);
        item.completionTimer = setTimer(() => {
          item.completionTimer = null;
          const latest = extractAssistantText(node);
          if (!latest) return;
          if (latest !== item.lastText) {
            processNode(node);
            return;
          }
          maybeMarkCompleted(node, item, latest);
        }, remainingMs + 50);
        return;
      }
      item.completionNotified = true;
      if (item.completionTimer) clearTimer(item.completionTimer);
      item.completionTimer = null;
      markResponseCompleted();
    }

    function reportEntry(node, item, text, chunks, preview, isAuto) {
      return reportChunks({
        node,
        text,
        messageKey: item.key,
        chunks,
        autoPreview: preview,
        capturedAt: now(),
      }, Boolean(isAuto));
    }

    function schedulePendingSend(node, item, preview) {
      if (item.idleTimer) clearTimer(item.idleTimer);
      const options = getPreviewOptions();
      const minChars = Math.max(1, Number(options.minChars || 40));
      const generationRetryMs = isResponseGenerating() ? 500 : 0;
      const completionRetryMs = preview.length < minChars ? 500 : 0;
      const remainingMs = Math.max(
        50,
        generationRetryMs,
        completionRetryMs,
        stableDelayForPreview(preview) - (now() - item.lastChangedAt) + 50,
      );
      item.idleTimer = setTimer(() => {
        item.idleTimer = null;
        if (item.sent) return;
        const latest = extractAssistantText(node);
        if (!latest) return;
        if (latest !== item.lastText) {
          processNode(node);
          return;
        }
        const { chunks, preview: pendingPreview } = previewParts(latest);
        if (!pendingPreview) return;
        if (!shouldSendNow(node, pendingPreview, now(), item)) {
          schedulePendingSend(node, item, pendingPreview);
          return;
        }
        item.sent = true;
        if (node.dataset) node.dataset[sentFlag] = '1';
        void reportEntry(node, item, latest, chunks, pendingPreview, isAutoEnabled());
        maybeMarkCompleted(node, item, latest);
      }, remainingMs);
    }

    function processNode(node) {
      const text = extractAssistantText(node);
      if (!text) return false;
      const item = ensureElementState(node, text);
      if (isResponseGenerating()) item.generationObserved = true;
      if (item.sent) {
        if (text === item.lastText) {
          maybeMarkCompleted(node, item, text);
          return true;
        }
        item.lastText = text;
        item.lastChangedAt = now();
        const { chunks, preview } = previewParts(text);
        if (chunks.length && preview) {
          void reportEntry(node, item, text, chunks, preview, false);
          maybeMarkCompleted(node, item, text);
        }
        return true;
      }
      if (!initializedElements.has(node)) {
        initializedElements.add(node);
        item.lastText = text;
        item.lastChangedAt = now();
        if (!isAutoEnabled()) return true;
      }
      if (text !== item.lastText) {
        item.lastText = text;
        item.lastChangedAt = now();
      }
      const { chunks, preview } = previewParts(text);
      if (!preview) return false;
      if (shouldSendNow(node, preview, now(), item)) {
        item.sent = true;
        if (node.dataset) node.dataset[sentFlag] = '1';
        void reportEntry(node, item, text, chunks, preview, isAutoEnabled());
        maybeMarkCompleted(node, item, text);
        return true;
      }
      schedulePendingSend(node, item, preview);
      return true;
    }

    function markExistingMessagesAsSeen() {
      for (const node of getAssistantNodes()) {
        const text = extractAssistantText(node);
        initializedElements.add(node);
        clearStateTimers(stateByElement.get(node));
        stateByElement.set(node, createState(node, text, {
          sent: true,
          completionNotified: true,
        }));
        if (node.dataset) node.dataset[sentFlag] = '1';
      }
    }

    function reportLatestSnapshot() {
      const nodes = getAssistantNodes();
      if (!nodes.length) return false;
      const node = nodes[nodes.length - 1];
      const text = extractAssistantText(node);
      if (!text) return false;
      const item = ensureElementState(node, text);
      const { chunks, preview } = previewParts(text);
      if (!chunks.length || !preview) return false;
      return Promise.resolve(reportEntry(node, item, text, chunks, preview, false)).then(() => true);
    }

    function inspectLatestAssistant() {
      const nodes = getAssistantNodes();
      if (!nodes.length) return false;
      const result = processNode(nodes[nodes.length - 1]);
      afterInspectLatest();
      return result;
    }

    function scheduleInspect(mutations = []) {
      const generationEnded = Array.from(mutations || []).some((mutation) => Array.from(mutation.removedNodes || [])
        .some((node) => isGenerationControlNode(node)));
      if (generationEnded) {
        if (inspectTimer) clearTimer(inspectTimer);
        inspectTimer = null;
        inspectLatestAssistant();
        return;
      }
      if (inspectTimer) return;
      inspectTimer = setTimer(() => {
        inspectTimer = null;
        inspectLatestAssistant();
      }, inspectDelayMs);
    }

    function destroy() {
      if (inspectTimer) clearTimer(inspectTimer);
      inspectTimer = null;
      for (const node of getAssistantNodes()) clearStateTimers(stateByElement.get(node));
    }

    return {
      destroy,
      inspectLatestAssistant,
      markExistingMessagesAsSeen,
      processNode,
      rebaseline: markExistingMessagesAsSeen,
      reportLatestSnapshot,
      scheduleInspect,
      stateFor: (node) => stateByElement.get(node) || null,
    };
  }

  return { createAutoSpeechController };
}));
