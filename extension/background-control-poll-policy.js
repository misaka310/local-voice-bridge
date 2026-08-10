'use strict';

(function exposeBackgroundControlPollPolicy(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.BackgroundControlPollPolicy = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  const FAST_MIC_PHASES = new Set([
    'recording',
    'transcribing',
    'pending_send',
    'sending',
  ]);

  function intervalMs(micConversationEnabled, conversationPhase) {
    if (!micConversationEnabled) return 5000;
    return FAST_MIC_PHASES.has(String(conversationPhase || '').toLowerCase()) ? 100 : 500;
  }

  return Object.freeze({ intervalMs });
});
