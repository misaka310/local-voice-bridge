'use strict';

(function exposeGenerationCompletionEvidence(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceGenerationCompletionEvidence = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  function createCompletionEvidence(environment = {}) {
    const {
      getAssistantNodes,
      extractAssistantText,
      ensureState,
      hasCompletionControl,
      getStableKey,
      isResponseGenerating,
      isResponseError,
      stableMs,
    } = environment;
    const isGenerating = typeof isResponseGenerating === 'function' ? isResponseGenerating : () => false;
    const isError = typeof isResponseError === 'function' ? isResponseError : () => false;
    let baselineNode = null;
    let baselineText = '';
    let active = false;

    function reset() {
      baselineNode = null;
      baselineText = '';
      active = false;
    }

    function resetCandidate(item) {
      item.completionCandidateText = '';
      item.completionCandidateSince = 0;
      item.completionReason = '';
    }

    function getRestorableCompletionKey(node, text) {
      if (!node || !text || isGenerating() || !hasCompletionControl(node) || isError(node)) return '';
      return getStableKey(node);
    }

    function markGenerationObserved() {
      const nodes = getAssistantNodes();
      baselineNode = nodes.length ? nodes[nodes.length - 1] : null;
      baselineText = baselineNode ? extractAssistantText(baselineNode) : '';
      active = true;
      if (!baselineNode) return;
      const item = ensureState(baselineNode, baselineText);
      item.generationObserved = true;
      if (item.sent) {
        item.sent = false;
        item.suppressAuto = true;
        item.completionNotified = false;
      }
      resetCandidate(item);
    }

    function inherit(node, item, text) {
      if (!active || item.generationObserved) return false;
      if (node === baselineNode && text === baselineText) return false;
      item.generationObserved = true;
      reset();
      return true;
    }

    function observe(node, item, text, timestamp, generating) {
      if (generating) {
        item.generationObserved = true;
        resetCandidate(item);
        return { generating: true, confirmed: false, reason: '' };
      }
      if (!hasCompletionControl(node) && !item.generationObserved) {
        resetCandidate(item);
        return { generating: false, confirmed: false, reason: '' };
      }
      const reason = item.generationObserved
        ? (hasCompletionControl(node) ? 'generation-ended-with-action-control' : 'generation-ended-stable')
        : 'action-control';
      if (item.completionCandidateText !== text || item.completionReason !== reason) {
        item.completionCandidateText = text;
        item.completionCandidateSince = timestamp;
        item.completionReason = reason;
      }
      return {
        generating: false,
        confirmed: timestamp - item.completionCandidateSince >= stableMs,
        reason,
      };
    }

    function hasResponseProgress(node, text) {
      return node !== baselineNode || text !== baselineText || hasCompletionControl(node);
    }

    return Object.freeze({
      active: () => active,
      hasResponseProgress,
      inherit,
      markGenerationObserved,
      observe,
      reset,
      resetCandidate,
      getRestorableCompletionKey,
    });
  }

  function createTransitionTracker(environment = {}) {
    let active = false;
    return Object.freeze({
      sync(generating) {
        if (generating) {
          if (!active) {
            active = true;
            environment.onStart();
          }
          environment.onGenerating();
          return;
        }
        active = false;
        environment.onEnded();
      },
    });
  }

  return Object.freeze({ createCompletionEvidence, createTransitionTracker });
}));
