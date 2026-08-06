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
    const reportChunks = environment.reportChunks;
    const markResponseCompleted = environment.markResponseCompleted;
    const isAutoEnabled = environment.isAutoEnabled;
    const isGenerationControlNode = environment.isGenerationControlNode;
    const isCompletionControlNode = typeof environment.isCompletionControlNode === 'function'
      ? environment.isCompletionControlNode
      : () => false;
    const requestRecheck = typeof environment.requestRecheck === 'function'
      ? environment.requestRecheck
      : () => {};
    const afterInspectLatest = typeof environment.afterInspectLatest === 'function'
      ? environment.afterInspectLatest
      : () => {};
    const now = typeof environment.now === 'function' ? environment.now : Date.now;
    const setTimer = environment.setTimeout || globalThis.setTimeout.bind(globalThis);
    const clearTimer = environment.clearTimeout || globalThis.clearTimeout.bind(globalThis);
    const sentFlag = String(environment.sentFlag || 'localVoiceSent');
    const inspectDelayMs = Math.max(0, Number(environment.inspectDelayMs || 1000));
    const generationRecheckMs = Math.max(5000, Number(environment.generationRecheckMs || 30000));
    const completionEvidenceStableMs = Math.max(
      100,
      Number(environment.completionEvidenceStableMs || 1200),
    );
    const stateByElement = new WeakMap();
    const initializedElements = new WeakSet();
    const pendingElements = new WeakSet();
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
        completionCandidateText: '',
        completionCandidateSince: 0,
        completionReason: '',
        lastText: text,
        lastChangedAt: now(),
        idleTimer: null,
        ...overrides,
      };
    }

    function clearStateTimers(item) {
      if (!item) return;
      if (item.idleTimer) clearTimer(item.idleTimer);
      item.idleTimer = null;
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

    function resetCompletionCandidate(item) {
      item.completionCandidateText = '';
      item.completionCandidateSince = 0;
      item.completionReason = '';
    }

    function observeCompletion(node, item, text, timestamp) {
      const generating = Boolean(isResponseGenerating());
      if (generating) {
        item.generationObserved = true;
        resetCompletionCandidate(item);
        return { generating: true, confirmed: false, reason: '' };
      }
      if (!hasResponseCompletionControl(node)) {
        resetCompletionCandidate(item);
        return { generating: false, confirmed: false, reason: '' };
      }
      const reason = item.generationObserved
        ? 'generation-ended-with-action-control'
        : 'action-control';
      if (item.completionCandidateText !== text || item.completionReason !== reason) {
        item.completionCandidateText = text;
        item.completionCandidateSince = timestamp;
        item.completionReason = reason;
      }
      return {
        generating: false,
        confirmed: timestamp - item.completionCandidateSince >= completionEvidenceStableMs,
        reason,
      };
    }

    function shouldSendNow(node, text, preview, timestamp, item) {
      if (!preview) return false;
      const completion = observeCompletion(node, item, text, timestamp);
      if (!completion.confirmed) return false;
      return timestamp - item.lastChangedAt >= stableDelayForPreview(preview);
    }

    function notifyCompleted(item) {
      if (item.completionNotified) return;
      item.completionNotified = true;
      markResponseCompleted();
    }

    function reportEntry(node, item, text, chunks, preview, isAuto) {
      return reportChunks({
        node,
        text,
        messageKey: item.key,
        chunks,
        autoPreview: preview,
        completionReason: item.completionReason,
        completionObservedAt: item.completionCandidateSince,
        capturedAt: now(),
      }, Boolean(isAuto));
    }

    function pendingDelay(preview, item) {
      if (isResponseGenerating()) return generationRecheckMs;
      const timestamp = now();
      const stableRemaining = stableDelayForPreview(preview) - (timestamp - item.lastChangedAt);
      const completionRemaining = item.completionCandidateText
        ? completionEvidenceStableMs - (timestamp - item.completionCandidateSince)
        : 500;
      return Math.max(50, Math.min(500, Math.max(stableRemaining, completionRemaining)));
    }

    function schedulePendingSend(node, item, preview) {
      const delay = pendingDelay(preview, item);
      if (item.idleTimer) clearTimer(item.idleTimer);
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
        if (!shouldSendNow(node, latest, pendingPreview, now(), item)) {
          schedulePendingSend(node, item, pendingPreview);
          return;
        }
        item.sent = true;
        if (node.dataset) node.dataset[sentFlag] = '1';
        void reportEntry(node, item, latest, chunks, pendingPreview, isAutoEnabled());
        notifyCompleted(item);
      }, delay);
      if (delay <= 5000) void Promise.resolve(requestRecheck(delay)).catch(() => {});
    }

    function processNode(node) {
      const text = extractAssistantText(node);
      if (!text) {
        pendingElements.add(node);
        return false;
      }
      const item = ensureElementState(node, text);
      if (isResponseGenerating()) {
        item.generationObserved = true;
        resetCompletionCandidate(item);
      }
      if (item.sent) {
        if (text === item.lastText) return true;
        item.lastText = text;
        item.lastChangedAt = now();
        resetCompletionCandidate(item);
        const { chunks, preview } = previewParts(text);
        if (chunks.length && preview) void reportEntry(node, item, text, chunks, preview, false);
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
        resetCompletionCandidate(item);
      }
      const { chunks, preview } = previewParts(text);
      if (!preview) return false;
      if (shouldSendNow(node, text, preview, now(), item)) {
        item.sent = true;
        if (node.dataset) node.dataset[sentFlag] = '1';
        void reportEntry(node, item, text, chunks, preview, isAutoEnabled());
        notifyCompleted(item);
        return true;
      }
      schedulePendingSend(node, item, preview);
      return true;
    }

    function markExistingMessagesAsSeen() {
      for (const node of getAssistantNodes()) {
        const text = extractAssistantText(node);
        if (!text) {
          pendingElements.add(node);
          continue;
        }
        if (pendingElements.has(node)) continue;
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
      const completionControlAdded = Array.from(mutations || []).some((mutation) => Array.from(mutation.addedNodes || [])
        .some((node) => isCompletionControlNode(node)));
      if (generationEnded || completionControlAdded) {
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
