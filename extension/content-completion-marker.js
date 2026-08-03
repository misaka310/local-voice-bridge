'use strict';

(function initContentCompletionMarker(global) {
  const TITLE_PREFIX = '● ';
  const SESSION_KEY = 'localVoiceCompletionPending';
  const FAVICON_ID = 'local-voice-completion-favicon';
  const FAVICON_DATA_URL = `data:image/svg+xml,${encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#facc15"/><path d="M8 16.5l5 5L24 10" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  )}`;

  function create(ctx) {
    let pending = false;
    let baseTitle = '';
    let titleObserver = null;

    function stripPrefix(title) {
      const value = String(title || '');
      return value.startsWith(TITLE_PREFIX) ? value.slice(TITLE_PREFIX.length) : value;
    }

    function getPlainDocumentTitle() {
      return stripPrefix(ctx.document.title) || baseTitle || '';
    }

    async function isTabActivelyViewed() {
      try {
        const attention = await Promise.race([
          ctx.runtimeMessage('tab-attention-state'),
          new Promise((resolve) => ctx.window.setTimeout(() => resolve(null), 500)),
        ]);
        return Boolean(attention && attention.active && ctx.document.hasFocus());
      } catch (_error) {
        return false;
      }
    }

    function persist() {
      try {
        if (pending) ctx.sessionStorage.setItem(SESSION_KEY, '1');
        else ctx.sessionStorage.removeItem(SESSION_KEY);
      } catch (_error) {}
    }

    function ensureFavicon() {
      if (!ctx.document.head) return;
      let favicon = ctx.document.getElementById(FAVICON_ID);
      if (!favicon) {
        favicon = ctx.document.createElement('link');
        favicon.id = FAVICON_ID;
        favicon.rel = 'icon';
        favicon.type = 'image/svg+xml';
        favicon.href = FAVICON_DATA_URL;
      }
      if (ctx.document.head.lastElementChild !== favicon) ctx.document.head.appendChild(favicon);
    }

    function sync() {
      const currentTitle = stripPrefix(ctx.document.title);
      if (currentTitle) baseTitle = currentTitle;
      if (!pending) {
        if (ctx.document.title.startsWith(TITLE_PREFIX) && baseTitle) ctx.document.title = baseTitle;
        ctx.document.getElementById(FAVICON_ID)?.remove();
        return;
      }
      if (baseTitle) {
        const markedTitle = `${TITLE_PREFIX}${baseTitle}`;
        if (ctx.document.title !== markedTitle) ctx.document.title = markedTitle;
      }
      ensureFavicon();
    }

    function setPending(nextPending) {
      pending = Boolean(nextPending);
      persist();
      sync();
    }

    function clear() {
      if (!pending && !ctx.document.getElementById(FAVICON_ID)) return;
      setPending(false);
    }

    function markResponseCompleted() {
      setPending(true);
      void isTabActivelyViewed().then((active) => {
        if (active) clear();
      });
    }

    async function initialize() {
      baseTitle = stripPrefix(ctx.document.title);
      try {
        pending = ctx.sessionStorage.getItem(SESSION_KEY) === '1';
      } catch (_error) {
        pending = false;
      }
      if (await isTabActivelyViewed()) setPending(false);
      else sync();
      titleObserver = new ctx.MutationObserver(sync);
      titleObserver.observe(ctx.document.head, { childList: true, subtree: true, characterData: true });
    }

    function dispose() {
      if (titleObserver) titleObserver.disconnect();
      titleObserver = null;
    }

    return {
      getPlainDocumentTitle,
      isTabActivelyViewed,
      sync,
      clear,
      markResponseCompleted,
      initialize,
      dispose,
    };
  }

  global.LocalVoiceCompletionMarker = Object.freeze({ create });
})(globalThis);
