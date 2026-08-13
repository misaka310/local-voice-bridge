'use strict';

(function initBackgroundNetworkMonitor(global) {
  const URL_PATTERNS = Object.freeze([
    'https://chatgpt.com/*',
    'https://chat.openai.com/*',
  ]);
  const ALLOWED_HOSTS = new Set(['chatgpt.com', 'chat.openai.com']);

  function sanitizeDetails(details = {}) {
    let parsed;
    try {
      parsed = new URL(String(details.url || ''));
    } catch (_error) {
      return null;
    }
    const host = String(parsed.hostname || '').toLowerCase();
    if (!ALLOWED_HOSTS.has(host)) return null;

    const statusCode = Number(details.statusCode);
    if (!Number.isFinite(statusCode)) return null;
    const tabId = Number.isInteger(details.tabId) ? details.tabId : -1;
    const timeStamp = Number(details.timeStamp);
    const observedAt = Number.isFinite(timeStamp)
      ? new Date(timeStamp).toISOString()
      : new Date().toISOString();

    return {
      observedAt,
      method: String(details.method || 'GET').trim().toUpperCase().slice(0, 16),
      statusCode: Math.trunc(statusCode),
      type: String(details.type || 'other').trim().toLowerCase().slice(0, 32),
      tabId,
      host,
      path: String(parsed.pathname || '/').slice(0, 2048),
      synthetic: false,
    };
  }

  function shouldPersist(event) {
    if (!event || typeof event !== 'object') return false;
    const statusCode = Number(event.statusCode);
    if (Number.isFinite(statusCode) && statusCode >= 400) return true;
    return String(event.path || '').toLowerCase().includes('conversation');
  }

  function create(options = {}) {
    const webRequest = options.webRequest;
    const postEvent = options.postEvent;
    let started = false;

    function onCompleted(details) {
      const event = sanitizeDetails(details);
      if (!shouldPersist(event) || typeof postEvent !== 'function') return;
      Promise.resolve(postEvent(event)).catch(() => {});
    }

    function start() {
      if (started || !webRequest?.onCompleted?.addListener) return false;
      webRequest.onCompleted.addListener(onCompleted, { urls: URL_PATTERNS });
      started = true;
      return true;
    }

    return { start, onCompleted };
  }

  global.BackgroundNetworkMonitor = Object.freeze({
    URL_PATTERNS,
    sanitizeDetails,
    shouldPersist,
    create,
  });
})(globalThis);
