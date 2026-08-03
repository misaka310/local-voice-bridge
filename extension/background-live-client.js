'use strict';

(function exposeBackgroundLiveClient(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.BackgroundLiveClient = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const MESSAGE_TYPES = new Set(['live-submission', 'live-chunk', 'live-interrupt', 'live-state']);

  function create(environment = {}) {
    const fetchFunction = environment.fetch;
    const getSettings = environment.getSettings;
    const buildUrl = environment.buildUrl;
    if (typeof fetchFunction !== 'function') throw new Error('fetch is required');
    if (typeof getSettings !== 'function') throw new Error('getSettings is required');
    if (typeof buildUrl !== 'function') throw new Error('buildUrl is required');

    async function request(pathname, body = null) {
      const settings = await getSettings();
      const response = await fetchFunction(buildUrl(settings, pathname), {
        method: body ? 'POST' : 'GET',
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
        cache: 'no-store',
      });
      const payload = await response.json().catch(() => ({}));
      return {
        ok: Boolean(response.ok && payload && payload.ok !== false),
        status: Number(response.status) || 0,
        payload: payload && typeof payload === 'object' ? payload : {},
      };
    }

    function bodyFor(message, senderTabId) {
      const body = message && message.payload && typeof message.payload === 'object'
        ? { ...message.payload }
        : {};
      if (Number.isInteger(senderTabId) && senderTabId > 0) body.tabId = senderTabId;
      return body;
    }

    async function handle(message, senderTabId, isRegisteredTab = true) {
      const type = String(message && message.type || '');
      if (!MESSAGE_TYPES.has(type)) return { handled: false };
      if (['live-submission', 'live-chunk'].includes(type) && (!senderTabId || !isRegisteredTab)) {
        return { handled: true, response: { ok: false, error: 'tab-not-registered' } };
      }
      if (type === 'live-state') {
        const result = await request('/v1/live/state');
        return {
          handled: true,
          response: result.ok
            ? { ok: true, payload: result.payload }
            : { ok: false, status: result.status, error: result.payload.error || `HTTP ${result.status}` },
        };
      }
      const paths = {
        'live-submission': '/v1/conversation/submission',
        'live-chunk': '/v1/live/chunks',
        'live-interrupt': '/v1/interrupt',
      };
      const body = bodyFor(message, senderTabId);
      if (type === 'live-submission') body.action = String(message.action || body.action || '');
      const result = await request(paths[type], body);
      if (result.ok) return { handled: true, response: { ok: true, payload: result.payload } };
      if (type === 'live-chunk' && result.status === 429) {
        return {
          handled: true,
          response: {
            ok: true,
            payload: {
              ok: false,
              retry: true,
              retryAfterMs: Number(result.payload.retryAfterMs) || 100,
              error: result.payload.error || 'live-backpressure',
            },
          },
        };
      }
      return {
        handled: true,
        response: {
          ok: false,
          status: result.status,
          error: result.payload.error || `HTTP ${result.status}`,
        },
      };
    }

    return { request, bodyFor, handle };
  }

  return { MESSAGE_TYPES, create };
}));
