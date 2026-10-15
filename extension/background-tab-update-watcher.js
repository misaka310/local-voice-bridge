'use strict';

(function exposeBackgroundTabUpdateWatcher(global) {
  const WATCHER_KEY = '__localVoiceBackgroundTabUpdateWatcher';
  const STATUS_KEY = 'localVoiceTerminalStatus';
  const LEGACY_STATUS_KEY = 'localVoiceCompletionPending';
  const GENERATING_SELECTOR = [
    '[data-testid="stop-button"]',
    'button[aria-label="Stop"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label="Stop streaming"]',
    'button[title="Stop"]',
    'button[aria-label="停止"]',
    'button[aria-label="生成を停止"]',
    'button[aria-label="応答を停止"]',
    'button[aria-label="ストリーミングを停止"]',
  ].join(',');
  const COMPLETE_SELECTOR = [
    '.turn-action-controls',
    'button[data-testid="copy-turn-action-button"]',
    'button[data-testid*="copy"]',
    'button[aria-label="Copy"]',
    'button[aria-label^="Copy"]',
    'button[aria-label*="コピー"]',
  ].join(',');
  const ASSISTANT_SELECTOR = [
    '[data-message-author-role="assistant"]',
    '[data-conversation-role="assistant"]',
  ].join(',');
  const COMPOSER_SELECTORS = [
    '#prompt-textarea',
    'textarea[data-id="root"]',
    'textarea[placeholder]',
    '[contenteditable="true"][data-virtualkeyboard]',
    'div[contenteditable="true"].ProseMirror',
  ];
  const COMPOSER_ATTACHMENT_SELECTOR = [
    'img',
    '[data-testid*="attachment-preview"]',
    '[data-testid*="file-preview"]',
    '[data-testid*="uploaded-file"]',
    '[aria-label*="Remove file"]',
    '[aria-label*="Remove attachment"]',
    '[aria-label*="添付を削除"]',
  ].join(',');

  function hasResponseGeneration(documentObject) {
    return Boolean(documentObject && documentObject.querySelector(GENERATING_SELECTOR));
  }

  function latestAssistantHasCompletionControl(documentObject) {
    if (!documentObject || typeof documentObject.querySelectorAll !== 'function') return false;
    const assistants = Array.from(documentObject.querySelectorAll(ASSISTANT_SELECTOR));
    const latest = assistants[assistants.length - 1];
    if (!latest) return false;
    const turn = latest.closest?.('[data-testid^="conversation-turn-"]')
      || latest.closest?.('article')
      || latest.closest?.('[data-conversation-role="assistant"]')
      || latest;
    return Boolean(turn && turn.querySelector?.(COMPLETE_SELECTOR));
  }

  function inspect(documentObject) {
    if (!documentObject || documentObject.readyState !== 'complete' || !documentObject.body) {
      return { safe: false, reason: 'document-not-ready' };
    }
    if (hasResponseGeneration(documentObject)) return { safe: false, reason: 'response-generating' };

    const composers = COMPOSER_SELECTORS.flatMap((selector) => (
      Array.from(documentObject.querySelectorAll(selector) || [])
    ));
    if (!composers.length) return { safe: false, reason: 'composer-not-found' };
    const hasDraft = composers.some((composer) => {
      const tagName = String(composer.tagName || '').toUpperCase();
      const value = tagName === 'TEXTAREA' || tagName === 'INPUT'
        ? composer.value
        : composer.innerText !== undefined ? composer.innerText : composer.textContent;
      return String(value || '').replace(/\u00a0/g, ' ').trim() !== '';
    });
    if (hasDraft) return { safe: false, reason: 'composer-not-empty' };

    const hasSelectedFile = Array.from(documentObject.querySelectorAll('input[type="file"]') || [])
      .some((input) => Number(input.files && input.files.length) > 0);
    if (hasSelectedFile) return { safe: false, reason: 'file-attached' };
    const hasComposerAttachment = composers.some((composer) => {
      let container = composer.closest?.('form') || composer;
      if (container === composer) {
        for (let depth = 0; depth < 4 && container.parentElement; depth += 1) {
          container = container.parentElement;
        }
      }
      return Boolean(container.querySelector?.(COMPOSER_ATTACHMENT_SELECTOR));
    });
    if (hasComposerAttachment) return { safe: false, reason: 'attachment-pending' };
    return { safe: true, reason: 'idle' };
  }

  function create(ctx) {
    let started = false;
    let requestPending = false;
    let timer = null;
    let observer = null;
    let sawGeneration = hasResponseGeneration(ctx.document);

    function persistCompletionIfNeeded() {
      if (!sawGeneration) return true;
      const error = Boolean(ctx.document.querySelector(
        '[data-testid="response-error"], [data-testid="conversation-turn-error"]',
      ));
      if (!error && !latestAssistantHasCompletionControl(ctx.document)) return false;
      try {
        ctx.sessionStorage.removeItem(LEGACY_STATUS_KEY);
        ctx.sessionStorage.setItem(STATUS_KEY, error ? 'error' : 'complete');
      } catch (_error) {
        return false;
      }
      sawGeneration = false;
      return true;
    }

    function check() {
      if (!started || requestPending) return false;
      if (hasResponseGeneration(ctx.document)) {
        sawGeneration = true;
        return false;
      }
      if (!persistCompletionIfNeeded()) return false;
      if (!inspect(ctx.document).safe) return false;
      requestPending = true;
      ctx.sendMessage({
        type: 'bridge-page-ready-for-update',
        contentScriptVersion: ctx.contentScriptVersion,
      }, (response) => {
        requestPending = false;
        if (response && response.ok && response.reloading) return;
        if (response && response.retry === true) scheduleCheck();
      });
      return true;
    }

    function scheduleCheck() {
      if (!started || timer !== null) return;
      timer = ctx.window.setTimeout(() => {
        timer = null;
        check();
      }, 250);
    }

    function start() {
      if (started) return false;
      started = true;
      sawGeneration = hasResponseGeneration(ctx.document);
      observer = new ctx.MutationObserver(scheduleCheck);
      observer.observe(ctx.document.documentElement || ctx.document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: [
          'aria-label', 'title', 'data-testid', 'data-message-author-role', 'data-conversation-role',
        ],
      });
      ctx.document.addEventListener('visibilitychange', scheduleCheck);
      ctx.document.addEventListener('input', scheduleCheck, true);
      ctx.document.addEventListener('change', scheduleCheck, true);
      ctx.window.addEventListener('blur', scheduleCheck);
      scheduleCheck();
      return true;
    }

    function dispose() {
      if (timer !== null) ctx.window.clearTimeout(timer);
      timer = null;
      observer?.disconnect();
      observer = null;
      if (started) {
        ctx.document.removeEventListener('visibilitychange', scheduleCheck);
        ctx.document.removeEventListener('input', scheduleCheck, true);
        ctx.document.removeEventListener('change', scheduleCheck, true);
        ctx.window.removeEventListener('blur', scheduleCheck);
      }
      started = false;
    }

    return {
      contentScriptVersion: String(ctx.contentScriptVersion || ''),
      start,
      check,
      dispose,
      inspect: () => inspect(ctx.document),
    };
  }

  const api = Object.freeze({ create, inspect, hasResponseGeneration, latestAssistantHasCompletionControl });
  global.BackgroundTabUpdateWatcher = api;

  if (global.chrome && global.document && global.window && global.sessionStorage) {
    const version = String(global.chrome.runtime.getManifest().version || '');
    if (!global[WATCHER_KEY] || global[WATCHER_KEY].contentScriptVersion !== version) {
      global[WATCHER_KEY]?.dispose?.();
      const watcher = create({
        document: global.document,
        window: global.window,
        sessionStorage: global.sessionStorage,
        runtime: global.chrome.runtime,
        contentScriptVersion: String(global.chrome.runtime.getManifest().version || ''),
        sendMessage(message, callback) { global.chrome.runtime.sendMessage(message, callback); },
        MutationObserver: global.MutationObserver,
      });
      global[WATCHER_KEY] = watcher;
    }
    if (!global.__localVoiceBackgroundTabUpdateMessageListener) {
      global.__localVoiceBackgroundTabUpdateMessageListener = true;
      global.chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
        if (!message || typeof message.type !== 'string') return false;
        if (message.type === 'bridge-watch-for-update') {
          const watcher = global[WATCHER_KEY];
          if (String(message.contentScriptVersion || '') !== watcherVersion()
            || !watcher || watcher.contentScriptVersion !== watcherVersion()) {
            sendResponse({ ok: false, reason: 'version-mismatch' });
            return false;
          }
          watcher.start();
          sendResponse({ ok: true, watching: true });
          return false;
        }
        const watcher = global[WATCHER_KEY];
        if (watcher && [
          'playback-completed', 'playback-stopped', 'playback-error', 'state-update',
        ].includes(message.type)) watcher.check();
        return false;
      });
    }
  }

  function watcherVersion() {
    try { return String(global.chrome.runtime.getManifest().version || ''); }
    catch (_error) { return ''; }
  }
})(globalThis);
