'use strict';

(function initContentCompletionMarker(global) {
  const LEGACY_TITLE_PREFIX = '● ';
  const LEGACY_SESSION_KEY = 'localVoiceCompletionPending';
  const SESSION_KEY = 'localVoiceTerminalStatus';
  const FAVICON_ID = 'local-voice-completion-favicon';
  const ORIGINAL_REL_ATTRIBUTE = 'data-local-voice-original-rel';
  const STATUS_SVG = Object.freeze({
    new: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#0891b2"/><path d="M16 7v18M7 16h18" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/></svg>',
    generating: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#2563eb"/><circle cx="9" cy="16" r="2.2" fill="#fff"/><circle cx="16" cy="16" r="2.2" fill="#fff"/><circle cx="23" cy="16" r="2.2" fill="#fff"/></svg>',
    complete: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#facc15"/><path d="M8 16.5l5 5L24 10" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    playing: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#16a34a"/><path d="M7 13h5l6-5v16l-6-5H7z" fill="#fff"/><path d="M21 12c2 2 2 6 0 8M24 9c4 4 4 10 0 14" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></svg>',
    error: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#dc2626"/><path d="M16 8v11" stroke="#fff" stroke-width="4" stroke-linecap="round"/><circle cx="16" cy="24" r="2.2" fill="#fff"/></svg>',
  });
  const FAVICON_DATA_URLS = Object.freeze(Object.fromEntries(
    Object.entries(STATUS_SVG).map(([status, svg]) => [status, `data:image/svg+xml,${encodeURIComponent(svg)}`]),
  ));

  function elementForNode(node) {
    if (!node) return null;
    if (node.nodeType === 1) return node;
    const parent = node.parentElement || node.parentNode;
    return parent && parent.nodeType === 1 ? parent : null;
  }

  function isRelevantFaviconNode(node) {
    const element = elementForNode(node);
    if (!element) return false;
    const links = [];
    if (String(element.tagName || '').toLowerCase() === 'link') links.push(element);
    if (typeof element.querySelectorAll === 'function') {
      try {
        links.push(...element.querySelectorAll('link'));
      } catch (_error) {}
    }
    return links.some((link) => (
      link.id === FAVICON_ID
      || link.hasAttribute?.(ORIGINAL_REL_ATTRIBUTE)
      || String(link.getAttribute?.('rel') || '')
        .split(/\s+/)
        .some((token) => token.toLowerCase() === 'icon')
    ));
  }

  function headMutationNeedsSync(mutations = []) {
    const batch = Array.from(mutations || []);
    if (!batch.length) return true;
    return batch.some((mutation) => {
      if (mutation.type === 'attributes') return isRelevantFaviconNode(mutation.target);
      if (mutation.type !== 'childList') return false;
      return [
        ...Array.from(mutation.addedNodes || []),
        ...Array.from(mutation.removedNodes || []),
      ].some(isRelevantFaviconNode);
    });
  }

  function create(ctx) {
    let terminalStatus = null;
    let newConversation = false;
    let generating = false;
    let playing = false;
    let baseTitle = '';
    let titleObserver = null;

    function stripPrefix(title) {
      const value = String(title || '');
      return value.startsWith(LEGACY_TITLE_PREFIX) ? value.slice(LEGACY_TITLE_PREFIX.length) : value;
    }

    function getPlainDocumentTitle() {
      return stripPrefix(ctx.document.title) || baseTitle || '';
    }

    function displayedStatus() {
      if (playing) return 'playing';
      if (terminalStatus === 'error') return 'error';
      if (generating) return 'generating';
      if (terminalStatus) return terminalStatus;
      if (newConversation) return 'new';
      return 'idle';
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

    function persistTerminalStatus() {
      try {
        ctx.sessionStorage.removeItem(LEGACY_SESSION_KEY);
        if (terminalStatus) ctx.sessionStorage.setItem(SESSION_KEY, terminalStatus);
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

    function ensureFavicon(status) {
      if (!ctx.document.head || !FAVICON_DATA_URLS[status]) return;
      let favicon = ctx.document.getElementById(FAVICON_ID);
      if (!favicon) {
        favicon = ctx.document.createElement('link');
        favicon.id = FAVICON_ID;
        favicon.rel = 'icon';
        favicon.type = 'image/svg+xml';
      }
      if (favicon.getAttribute('data-local-voice-status') !== status) {
        favicon.setAttribute('data-local-voice-status', status);
      }
      if (favicon.href !== FAVICON_DATA_URLS[status]) favicon.href = FAVICON_DATA_URLS[status];
      if (favicon.parentNode !== ctx.document.head) ctx.document.head.appendChild(favicon);
    }

    function sync() {
      const currentTitle = stripPrefix(ctx.document.title);
      if (currentTitle) {
        baseTitle = currentTitle;
        if (ctx.document.title !== currentTitle) ctx.document.title = currentTitle;
      }
      const status = displayedStatus();
      if (status === 'idle') {
        ctx.document.getElementById(FAVICON_ID)?.remove();
        restoreOriginalFavicons();
        return;
      }
      suppressOriginalFavicons();
      ensureFavicon(status);
    }

    function setTerminalStatus(status) {
      terminalStatus = status === 'complete' || status === 'error' ? status : null;
      persistTerminalStatus();
      sync();
    }

    function setNewConversation(value) {
      const next = Boolean(value);
      if (newConversation === next) return;
      newConversation = next;
      sync();
    }

    function acknowledge() {
      if (!terminalStatus && !ctx.document.getElementById(FAVICON_ID)) return;
      setTerminalStatus(null);
    }

    function markResponseGenerating() {
      if (generating) return;
      generating = true;
      setTerminalStatus(null);
    }

    function markResponseGenerationEnded() {
      if (!generating) return;
      generating = false;
      sync();
    }

    function markResponseCompleted() {
      generating = false;
      setTerminalStatus('complete');
      void isTabActivelyViewed().then((active) => {
        if (active) acknowledge();
      });
    }

    function markResponseError() {
      generating = false;
      setTerminalStatus('error');
    }

    function markPlaybackStarted() {
      playing = true;
      sync();
    }

    function markPlaybackCompleted() {
      playing = false;
      sync();
    }

    function markPlaybackError() {
      playing = false;
      sync();
    }

    function markPlaybackStopped() {
      playing = false;
      sync();
    }

    async function initialize() {
      baseTitle = stripPrefix(ctx.document.title);
      try {
        const stored = String(ctx.sessionStorage.getItem(SESSION_KEY) || '');
        terminalStatus = stored === 'complete' || stored === 'error'
          ? stored
          : ctx.sessionStorage.getItem(LEGACY_SESSION_KEY) === '1' ? 'complete' : null;
      } catch (_error) {
        terminalStatus = null;
      }
      persistTerminalStatus();
      if (await isTabActivelyViewed()) setTerminalStatus(null);
      else sync();
      titleObserver = new ctx.MutationObserver((mutations) => {
        if (headMutationNeedsSync(mutations)) sync();
      });
      titleObserver.observe(ctx.document.head, {
        childList: true,
        subtree: true,
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
      displayedStatus,
      sync,
      setNewConversation,
      clear: acknowledge,
      acknowledge,
      markResponseGenerating,
      markResponseGenerationEnded,
      markResponseCompleted,
      markResponseError,
      markPlaybackStarted,
      markPlaybackCompleted,
      markPlaybackError,
      markPlaybackStopped,
      initialize,
      dispose,
    };
  }

  global.LocalVoiceCompletionMarker = Object.freeze({ create, headMutationNeedsSync });
})(globalThis);
