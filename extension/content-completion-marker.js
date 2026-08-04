'use strict';

(function initContentCompletionMarker(global) {
  const LEGACY_TITLE_PREFIX = '● ';
  const SESSION_KEY = 'localVoiceCompletionPending';
  const FAVICON_ID = 'local-voice-completion-favicon';
  const ORIGINAL_REL_ATTRIBUTE = 'data-local-voice-original-rel';
  const FAVICON_DATA_URL = `data:image/svg+xml,${encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#facc15"/><path d="M8 16.5l5 5L24 10" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  )}`;

  function create(ctx) {
    let pending = false;
    let baseTitle = '';
    let titleObserver = null;

    function stripPrefix(title) {
      const value = String(title || '');
      return value.startsWith(LEGACY_TITLE_PREFIX) ? value.slice(LEGACY_TITLE_PREFIX.length) : value;
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

    function isIconRel(value) {
      return String(value || '')
        .split(/\s+/)
        .some((token) => token.toLowerCase() === 'icon');
    }

    function suppressOriginalFavicons() {
      for (const link of ctx.document.querySelectorAll('link')) {
        if (link.id === FAVICON_ID) continue;
        const storedRel = link.getAttribute(ORIGINAL_REL_ATTRIBUTE);
        const currentRel = link.getAttribute('rel');
        if (storedRel !== null) {
          if (isIconRel(currentRel)) link.removeAttribute('rel');
          continue;
        }
        if (!isIconRel(currentRel)) continue;
        link.setAttribute(ORIGINAL_REL_ATTRIBUTE, currentRel);
        link.removeAttribute('rel');
      }
    }

    function restoreOriginalFavicons() {
      for (const link of ctx.document.querySelectorAll(`link[${ORIGINAL_REL_ATTRIBUTE}]`)) {
        const originalRel = link.getAttribute(ORIGINAL_REL_ATTRIBUTE);
        if (originalRel) link.setAttribute('rel', originalRel);
        else link.removeAttribute('rel');
        link.removeAttribute(ORIGINAL_REL_ATTRIBUTE);
      }
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
      if (currentTitle) {
        baseTitle = currentTitle;
        if (ctx.document.title !== currentTitle) ctx.document.title = currentTitle;
      }
      if (!pending) {
        ctx.document.getElementById(FAVICON_ID)?.remove();
        restoreOriginalFavicons();
        return;
      }
      suppressOriginalFavicons();
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
      titleObserver.observe(ctx.document.head, {
        childList: true,
        subtree: true,
        characterData: true,
        attributes: true,
        attributeFilter: ['rel', 'href'],
      });
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
