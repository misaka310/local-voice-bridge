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
      stateFor,
      ensureState,
      hasCompletionControl,
      stableMs,
    } = environment;
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

    function markGenerationObserved() {
      const nodes = getAssistantNodes();
      baselineNode = nodes.length ? nodes[nodes.length - 1] : null;
      baselineText = baselineNode ? extractAssistantText(baselineNode) : '';
      active = true;
      if (!baselineNode) return;
      const existing = stateFor(baselineNode);
      if (existing && existing.sent) return;
      ensureState(baselineNode, baselineText).generationObserved = true;
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

    return Object.freeze({
      active: () => active,
      inherit,
      markGenerationObserved,
      observe,
      reset,
      resetCandidate,
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
