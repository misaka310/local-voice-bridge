'use strict';

(function exposeAssistantSnapshotSelection(root) {
  function latestNonEmpty(nodes, extractText) {
    if (!Array.isArray(nodes) || typeof extractText !== 'function') return null;
    for (let index = nodes.length - 1; index >= 0; index -= 1) {
      const node = nodes[index];
      const text = String(extractText(node) || '').trim();
      if (text) return { node, text };
    }
    return null;
  }

  const api = Object.freeze({ latestNonEmpty });
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceAssistantSnapshotSelection = api;
}(typeof globalThis !== 'undefined' ? globalThis : this));
