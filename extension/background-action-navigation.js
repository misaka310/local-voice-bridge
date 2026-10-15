'use strict';

(() => {
  const action = globalThis.chrome?.action?.onClicked;
  const openOptionsPage = globalThis.chrome?.runtime?.openOptionsPage;
  if (!action?.addListener || typeof openOptionsPage !== 'function') {
    return;
  }

  action.addListener(() => {
    try {
      const pending = globalThis.chrome.runtime.openOptionsPage();
      if (pending && typeof pending.catch === 'function') {
        pending.catch(() => {});
      }
    } catch (_error) {
      // Opening Options is a convenience path; never destabilize the worker.
    }
  });
})();
