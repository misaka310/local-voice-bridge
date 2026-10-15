'use strict';

(function exposeBackgroundStatePublisher(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundStatePublisher = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  function create(options = {}) {
    const now = typeof options.now === 'function' ? options.now : Date.now;
    const heartbeatMs = Math.max(5000, Number(options.heartbeatMs || 30000));
    let lastSignature = '';
    let lastPostedAt = Number.NEGATIVE_INFINITY;

    async function publishIfNeeded({ connected = false, state, publish }) {
      if (typeof publish !== 'function') throw new Error('publish is required');
      const signature = JSON.stringify(state);
      const timestamp = now();
      if (!connected || signature !== lastSignature || timestamp - lastPostedAt >= heartbeatMs) {
        await publish(state);
        lastSignature = signature;
        lastPostedAt = timestamp;
        return true;
      }
      return false;
    }

    return Object.freeze({ publishIfNeeded });
  }

  return Object.freeze({ create });
});
