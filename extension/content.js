(() => {
  const SETTINGS_VERSION = 11;
  const DEFAULT_SETTINGS = {
    settingsVersion: SETTINGS_VERSION,
    enabled: false,
    apiUrl: 'http://127.0.0.1:8717/v1/speak',
    healthUrl: 'http://127.0.0.1:8717/health',
    voiceProfile: 'irodori-v3',
    voiceId: '',
    referenceVoice: '',
    voicePrompt: '',
    voiceVolume: 0.6,
    previewMaxLines: 2,
    previewMaxChars: 80,
    previewMinChars: 40,
    previewStableMs: 1000,
    micConversationEnabled: false,
    sttModel: 'small',
    cancelGraceMs: 700,
    liveTtsProfile: 'speed',
  };
  const AUTO_SENT_FLAG = 'localVoiceSent';
  const COMPLETION_TITLE_PREFIX = '● ';
  const COMPLETION_SESSION_KEY = 'localVoiceCompletionPending';
  const COMPLETION_FAVICON_ID = 'local-voice-completion-favicon';
  const COMPLETION_FAVICON_DATA_URL = `data:image/svg+xml,${encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="9" fill="#facc15"/><path d="M8 16.5l5 5L24 10" fill="none" stroke="#111827" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  )}`;
  const RESPONSE_GENERATING_SELECTOR = [
    '[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="生成を停止"]',
    'button[aria-label="応答を停止"]',
    'button[aria-label="ストリーミングを停止"]',
  ].join(',');
  const RESPONSE_COMPLETE_SELECTOR = [
    'button[data-testid="copy-turn-action-button"]',
    'button[data-testid="good-response-turn-action-button"]',
    'button[data-testid="bad-response-turn-action-button"]',
    'button[aria-label="Copy"]',
    'button[aria-label="コピー"]',
    'button[aria-label="Good response"]',
    'button[aria-label="Bad response"]',
    'button[aria-label="Regenerate"]',
    'button[aria-label="再生成する"]',
  ].join(',');
  const DEFAULT_PET_ID = 'placeholder';
  const LEGACY_BROWSER_UI_STORAGE_KEYS = ['petMode', 'selectedPetId', 'petPosition', 'panelPosition', 'panelCollapsed'];

  const stateByElement = new WeakMap();
  const initializedElements = new WeakSet();
  let settings = { ...DEFAULT_SETTINGS };
  let enabled = false;
  let observer = null;
  let titleObserver = null;
  let inspectTimer = null;
  let currentAudio = null;
  let currentObjectUrl = null;
  let currentPlaybackToken = null;
  let currentPlaybackCancel = null;
  let currentConversationPhase = 'off';
  let pendingSendController = null;
  let liveController = null;
  let cancelOverlay = null;
  let isUiOwner = null;
  let completionMarkerPending = false;
  let baseDocumentTitle = '';
  let deliveryLedger = null;
  const textCore = globalThis.ContentTextCore;

  function normalizeText(text) {
    return String(text || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function stripCompletionTitlePrefix(title) {
    const value = String(title || '');
    return value.startsWith(COMPLETION_TITLE_PREFIX)
      ? value.slice(COMPLETION_TITLE_PREFIX.length)
      : value;
  }

  function getPlainDocumentTitle() {
    return stripCompletionTitlePrefix(document.title) || baseDocumentTitle || '';
  }

  async function isTabActivelyViewed() {
    try {
      const attention = await Promise.race([
        runtimeMessage('tab-attention-state'),
        new Promise((resolve) => window.setTimeout(() => resolve(null), 500)),
      ]);
      return Boolean(attention && attention.active && document.hasFocus());
    } catch (_error) {
      return false;
    }
  }

  function persistCompletionMarkerState() {
    try {
      if (completionMarkerPending) sessionStorage.setItem(COMPLETION_SESSION_KEY, '1');
      else sessionStorage.removeItem(COMPLETION_SESSION_KEY);
    } catch (_error) {}
  }

  function ensureCompletionFavicon() {
    if (!document.head) return;
    let favicon = document.getElementById(COMPLETION_FAVICON_ID);
    if (!favicon) {
      favicon = document.createElement('link');
      favicon.id = COMPLETION_FAVICON_ID;
      favicon.rel = 'icon';
      favicon.type = 'image/svg+xml';
      favicon.href = COMPLETION_FAVICON_DATA_URL;
    }
    if (document.head.lastElementChild !== favicon) document.head.appendChild(favicon);
  }

  function syncCompletionMarker() {
    const currentTitle = stripCompletionTitlePrefix(document.title);
    if (currentTitle) baseDocumentTitle = currentTitle;
    if (!completionMarkerPending) {
      if (document.title.startsWith(COMPLETION_TITLE_PREFIX) && baseDocumentTitle) {
        document.title = baseDocumentTitle;
      }
      document.getElementById(COMPLETION_FAVICON_ID)?.remove();
      return;
    }
    if (baseDocumentTitle) {
      const markedTitle = `${COMPLETION_TITLE_PREFIX}${baseDocumentTitle}`;
      if (document.title !== markedTitle) document.title = markedTitle;
    }
    ensureCompletionFavicon();
  }

  function setCompletionMarkerPending(nextPending) {
    completionMarkerPending = Boolean(nextPending);
    persistCompletionMarkerState();
    syncCompletionMarker();
  }

  function clearCompletionMarker() {
    if (!completionMarkerPending && !document.getElementById(COMPLETION_FAVICON_ID)) return;
    setCompletionMarkerPending(false);
  }

  function markResponseCompleted() {
    setCompletionMarkerPending(true);
    void isTabActivelyViewed().then((active) => {
      if (active) clearCompletionMarker();
    });
  }

  function isResponseGenerating() {
    return Boolean(document.querySelector(RESPONSE_GENERATING_SELECTOR));
  }

  function responseTurnForNode(node) {
    if (!node || typeof node.closest !== 'function') return null;
    return node.closest('[data-testid^="conversation-turn-"]') || node;
  }

  function hasResponseCompletionControl(node) {
    const turn = responseTurnForNode(node);
    return Boolean(turn && typeof turn.querySelector === 'function' && turn.querySelector(RESPONSE_COMPLETE_SELECTOR));
  }

  function observeResponseCompletion(node, item) {
    const generating = isResponseGenerating();
    if (generating) item.generationObserved = true;
    else if (item.generationObserved) item.generationCompleted = true;
    if (hasResponseCompletionControl(node)) item.completionControlObserved = true;
    return {
      generating,
      confirmed: Boolean(item.generationCompleted || item.completionControlObserved),
    };
  }

  function isTransientAssistantStatus(text) {
    const normalized = normalizeText(text).replace(/\s+/g, '');
    return /^(?:(?:\d+|個の)?画像を(?:分析|解析)(?:中|しています)|思考中|考え中|Thinking|Analyzing(?:the)?images?)(?:ストリーミングが中断されました。?完全なメッセージを待機しています)?(?:[.…。・]+)?$/i.test(normalized);
  }

  function normalizeMarkdownLine(line) {
    return String(line || '')
      .replace(/^>\s*/g, '')
      .replace(/^#{1,6}\s*/g, '')
      .replace(/^\s*[-*+]\s+/g, '')
      .replace(/^\s*\d+\.\s+/g, '')
      .replace(/\[(.*?)\]\((.*?)\)/g, '$1')
      .replace(/[*_~`]/g, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function clampVolume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return DEFAULT_SETTINGS.voiceVolume;
    return Math.min(1, Math.max(0, n));
  }

  function normalizeVoiceId(value) {
    return String(value || '').trim();
  }

  function resolveDesktopPetId(value) {
    const petId = normalizeVoiceId(value).toLowerCase();
    if (!petId || petId === 'none' || petId === '.' || petId === '..' || /[\\/]/.test(petId)) return DEFAULT_PET_ID;
    return petId;
  }

  function normalizeReferenceVoice(value) {
    const normalized = String(value || '').trim();
    if (!normalized || ['none', 'qwen3', 'qwen'].includes(normalized.toLowerCase())) return '';
    return normalized;
  }
  function storedReferenceVoice(raw) {
    const voiceId = normalizeReferenceVoice(raw && raw.voiceId);
    if (voiceId) return voiceId;
    return normalizeReferenceVoice(raw && raw.referenceVoice);
  }

  function clampInteger(value, fallback, minimum, maximum) {
    if (value === '' || value === null || value === undefined) return fallback;
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(maximum, Math.max(minimum, Math.round(number)));
  }

  function normalizeSttModel(value) {
    const normalized = String(value || '').trim();
    return ['small', 'medium', 'large-v3-turbo'].includes(normalized)
      ? normalized
      : DEFAULT_SETTINGS.sttModel;
  }

  function normalizeCancelGraceMs(value) {
    return clampInteger(value, DEFAULT_SETTINGS.cancelGraceMs, 0, 5000);
  }

  function normalizeLiveTtsProfile(value) {
    const normalized = String(value || '').trim().toLowerCase();
    return ['speed', 'balanced', 'bridge'].includes(normalized)
      ? normalized
      : DEFAULT_SETTINGS.liveTtsProfile;
  }

  async function sanitizeStoredSettings(raw) {
    const next = {
      ...DEFAULT_SETTINGS,
      ...raw,
      settingsVersion: SETTINGS_VERSION,
      model: DEFAULT_SETTINGS.voiceProfile,
      voiceId: storedReferenceVoice(raw),
      voiceProfile: DEFAULT_SETTINGS.voiceProfile,
      referenceVoice: storedReferenceVoice(raw),
      voicePrompt: '',
      previewMaxLines: clampInteger(raw.previewMaxLines, DEFAULT_SETTINGS.previewMaxLines, 1, 20),
      previewMaxChars: clampInteger(raw.previewMaxChars, DEFAULT_SETTINGS.previewMaxChars, 40, 1000),
      sttModel: normalizeSttModel(raw.sttModel),
      cancelGraceMs: normalizeCancelGraceMs(raw.cancelGraceMs),
      liveTtsProfile: normalizeLiveTtsProfile(raw.liveTtsProfile),
    };
    for (const key of LEGACY_BROWSER_UI_STORAGE_KEYS) delete next[key];
    if (globalThis.chrome && chrome.storage && chrome.storage.local) {
      await chrome.storage.local.set(next);
      await chrome.storage.local.remove(LEGACY_BROWSER_UI_STORAGE_KEYS);
    }
    return next;
  }

  function getCurrentVoiceProfile() {
    return DEFAULT_SETTINGS.voiceProfile;
  }

  function getCurrentReferenceVoice() {
    return normalizeReferenceVoice(settings.voiceId);
  }

  function getSpeakParams() {
    const referenceVoice = getCurrentReferenceVoice();
    return {
      voiceProfile: getCurrentVoiceProfile(),
      voiceId: referenceVoice,
      referenceVoice,
      voicePrompt: '',
    };
  }

  function splitChunkByMaxChars(text, maxChars, minChars) {
    const trimmed = normalizeText(text);
    if (!trimmed) return { head: '', tail: '' };
    if (trimmed.length <= maxChars) return { head: trimmed, tail: '' };
    const head = trimmed.slice(0, maxChars);
    const punctRegex = /[、。！？!?]/g;
    let punctMatch = null;
    for (const match of head.matchAll(punctRegex)) punctMatch = match;
    if (punctMatch && Number(punctMatch.index) >= Math.floor(minChars * 0.6)) {
      const cut = Number(punctMatch.index) + 1;
      return { head: normalizeText(trimmed.slice(0, cut)), tail: normalizeText(trimmed.slice(cut)) };
    }
    const soft = head.lastIndexOf(' ');
    if (soft >= Math.floor(minChars * 0.6)) {
      return { head: normalizeText(head.slice(0, soft)), tail: normalizeText(trimmed.slice(soft)) };
    }
    return { head: normalizeText(head), tail: normalizeText(trimmed.slice(maxChars)) };
  }

  function normalizeSpeakableLines(fullText) {
    let text = normalizeText(fullText);
    if (!text) return [];
    text = text.replace(/```[\s\S]*?```/g, ' ').replace(/`[^`]*`/g, ' ').replace(/\n{2,}/g, '\n');
    const lines = text
      .split('\n')
      .map((line) => normalizeMarkdownLine(line))
      .filter((line) => Boolean(line) && !isTransientAssistantStatus(line));
    return textCore.coalesceOrphanLines(lines, {
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
  }

  function buildPreviewSourceText(fullText, options = {}) {
    const maxLines = Number(options.maxLines || DEFAULT_SETTINGS.previewMaxLines);
    const lines = normalizeSpeakableLines(fullText);
    const picked = lines.slice(0, Math.max(1, maxLines));
    const merged = normalizeText(picked.join(' '));
    return merged;
  }

  function extractAutoPreview(fullText, options = {}) {
    const maxChars = Number(options.maxChars || DEFAULT_SETTINGS.previewMaxChars);
    const minChars = Number(options.minChars || DEFAULT_SETTINGS.previewMinChars);
    const merged = buildPreviewSourceText(fullText, options);
    if (!merged) return '';
    return splitChunkByMaxChars(merged, maxChars, minChars).head;
  }

  function splitSpeakChunks(fullText, options = {}) {
    const maxChars = Number(options.maxChars || DEFAULT_SETTINGS.previewMaxChars);
    const minChars = Number(options.minChars || DEFAULT_SETTINGS.previewMinChars);
    const maxLines = Math.max(1, Number(options.maxLines || DEFAULT_SETTINGS.previewMaxLines));
    const lines = normalizeSpeakableLines(fullText);
    const chunks = [];
    for (let index = 0; index < lines.length; index += maxLines) {
      let pending = normalizeText(lines.slice(index, index + maxLines).join(' '));
      while (pending) {
        const split = splitChunkByMaxChars(pending, maxChars, minChars);
        if (!split.head) break;
        chunks.push(split.head);
        if (!split.tail || split.tail === pending) break;
        pending = split.tail;
      }
    }
    return chunks;
  }

  function stableDelayForPreview(preview) {
    const minChars = Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars);
    const stableMs = Number(settings.previewStableMs || DEFAULT_SETTINGS.previewStableMs);
    return textCore.stableDelayForPreview(preview, { minChars, stableMs });
  }

  function shouldSendNow(node, preview, now, item) {
    if (!preview.length) return false;
    const minChars = Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars);
    const completion = observeResponseCompletion(node, item);
    if (!textCore.canFinalizePreview(preview, {
      minChars,
      completionConfirmed: completion.confirmed,
    })) return false;
    if (completion.generating && preview.length < minChars) return false;
    return now - item.lastChangedAt >= stableDelayForPreview(preview);
  }

  function schedulePendingAutoSend(node, item, preview) {
    if (item.idleTimer) clearTimeout(item.idleTimer);
    const minChars = Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars);
    const generationRetryMs = isResponseGenerating() ? 500 : 0;
    const completionRetryMs = preview.length < minChars ? 500 : 0;
    const remainingMs = Math.max(
      50,
      generationRetryMs,
      completionRetryMs,
      stableDelayForPreview(preview) - (Date.now() - item.lastChangedAt) + 50,
    );
    item.idleTimer = setTimeout(() => {
      item.idleTimer = null;
      if (item.sent) return;
      const latest = extractAssistantText(node);
      if (!latest) return;
      if (latest !== item.lastText) {
        processNode(node);
        return;
      }
      const pendingChunks = splitSpeakChunks(latest, {
        maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
        maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
        minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
      });
      const pendingPreview = extractAutoPreview(latest, {
        maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
        maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
        minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
      });
      if (!pendingPreview) return;
      if (!shouldSendNow(node, pendingPreview, Date.now(), item)) {
        schedulePendingAutoSend(node, item, pendingPreview);
        return;
      }
      item.sent = true;
      node.dataset[AUTO_SENT_FLAG] = '1';
      void reportChunks({ node, text: latest, messageKey: item.key, chunks: pendingChunks, autoPreview: pendingPreview, capturedAt: Date.now() }, Boolean(enabled && settings.enabled));
      maybeMarkResponseCompleted(node, item, latest);
    }, remainingMs);
  }

  function getAssistantNodes() {
    const primary = Array.from(document.querySelectorAll('[data-message-author-role="assistant"]'));
    if (primary.length > 0) return primary;
    return Array.from(document.querySelectorAll('article')).filter((node) => {
      const label = `${node.getAttribute('aria-label') || ''} ${node.textContent || ''}`.toLowerCase();
      return label.includes('assistant') || label.includes('chatgpt');
    });
  }

  function getStableKey(node) {
    const turn = node.closest('[data-testid^="conversation-turn-"]');
    const testId = turn && turn.getAttribute('data-testid');
    const messageId = node.getAttribute('data-message-id') || node.dataset.messageId;
    if (messageId) return messageId;
    if (testId) return testId;
    if (!node.__localVoiceBridgeId) node.__localVoiceBridgeId = `node-${Math.random().toString(36).slice(2)}`;
    return node.__localVoiceBridgeId;
  }

  function normalizedLinkLabel(value) {
    return normalizeText(value).toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function isBareHostLabel(link, label) {
    if (!label || label.length > 40) return false;
    const href = String(link.getAttribute('href') || '').trim();
    if (!href) return false;
    try {
      const parsed = new URL(href, 'https://chatgpt.com/');
      if (!/^https?:$/.test(parsed.protocol)) return false;
      const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
      const hostParts = host.split('.').filter(Boolean);
      const candidates = new Set([
        hostParts[0] || '',
        hostParts.slice(0, -1).join(''),
        host.replace(/\./g, ''),
      ].map(normalizedLinkLabel).filter(Boolean));
      return candidates.has(normalizedLinkLabel(label));
    } catch (_error) {
      return false;
    }
  }

  function removeDecorativeSourceLinks(clone) {
    const links = Array.from(clone.querySelectorAll('a'));
    const labelCounts = new Map();
    for (const link of links) {
      const label = normalizeText(link.innerText || link.textContent || '');
      const key = normalizedLinkLabel(label);
      if (key) labelCounts.set(key, (labelCounts.get(key) || 0) + 1);
    }
    for (const link of links) {
      const label = normalizeText(link.innerText || link.textContent || '');
      const key = normalizedLinkLabel(label);
      const repeatedBareLabel = /^[A-Za-z][A-Za-z0-9 ._+-]{1,39}$/.test(label)
        && Number(labelCounts.get(key) || 0) >= 3;
      if (repeatedBareLabel || isBareHostLabel(link, label)) link.remove();
    }
  }

  function extractAssistantText(node) {
    const clone = node.cloneNode(true);
    clone.querySelectorAll('pre, button, svg, menu, nav, script, style, textarea, input, select, sup').forEach((item) => item.remove());
    removeDecorativeSourceLinks(clone);
    const text = textCore.stripRepeatedUiLabels(normalizeText(clone.innerText || clone.textContent || ''));
    return isTransientAssistantStatus(text) ? '' : text;
  }

  function ensureElementState(node, text) {
    let item = stateByElement.get(node);
    if (!item) {
      const alreadySent = node.dataset[AUTO_SENT_FLAG] === '1';
      item = {
        key: getStableKey(node),
        sent: alreadySent,
        completionNotified: alreadySent,
        generationObserved: false,
        generationCompleted: false,
        completionControlObserved: false,
        lastText: text,
        lastChangedAt: Date.now(),
        idleTimer: null,
        completionTimer: null,
      };
      stateByElement.set(node, item);
    }
    return item;
  }

  function markExistingMessagesAsSeen() {
    for (const node of getAssistantNodes()) {
      const text = extractAssistantText(node);
      initializedElements.add(node);
      const item = stateByElement.get(node);
      if (item && item.idleTimer) clearTimeout(item.idleTimer);
      if (item && item.completionTimer) clearTimeout(item.completionTimer);
      stateByElement.set(node, {
        key: getStableKey(node),
        sent: true,
        completionNotified: true,
        generationObserved: false,
        generationCompleted: false,
        completionControlObserved: false,
        lastText: text,
        lastChangedAt: Date.now(),
        idleTimer: null,
        completionTimer: null,
      });
      node.dataset[AUTO_SENT_FLAG] = '1';
    }
  }

  function rebaselineAutoMessages() {
    markExistingMessagesAsSeen();
  }

  function runtimeMessage(type, extra = {}) {
    return new Promise((resolve, reject) => {
      if (!globalThis.chrome || !chrome.runtime || typeof chrome.runtime.sendMessage !== 'function') {
        reject(new Error('chrome.runtime is unavailable'));
        return;
      }
      chrome.runtime.sendMessage({ type, ...extra }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
          return;
        }
        if (!response || !response.ok) {
          reject(new Error((response && response.error) || 'unknown error'));
          return;
        }
        resolve(response.payload);
      });
    });
  }

  function ensureLiveController() {
    if (liveController) return liveController;
    const api = globalThis.LocalVoiceLiveContent;
    if (!api || typeof api.createLiveContentController !== 'function') return null;
    liveController = api.createLiveContentController({
      getLocation: () => location.href,
      getAssistantNodes,
      getStableKey,
      extractAssistantText,
      isResponseGenerating,
      runtimeMessage,
      crypto: globalThis.crypto,
      getVoiceSettings: () => ({
        liveTtsProfile: normalizeLiveTtsProfile(settings.liveTtsProfile),
        voiceId: getCurrentReferenceVoice(),
        referenceVoice: getCurrentReferenceVoice(),
        voicePrompt: String(settings.voicePrompt || ''),
        voiceVolume: clampVolume(settings.voiceVolume),
      }),
      onState: (state) => {
        const labels = {
          arming: '送信関連付けを保存中',
          armed: '送信準備完了',
          committed: 'ChatGPT応答待ち',
          bound: '返答を逐次読み上げ中',
          invalidated: 'Live会話を取り消しました',
          interrupted: 'Live会話を停止しました',
        };
        if (labels[state.phase]) {
          reportConversationState({
            phase: state.phase === 'bound' ? 'responding' : state.phase,
            statusText: labels[state.phase],
            error: '',
            sttModel: settings.sttModel,
          });
        }
      },
    });
    return liveController;
  }

  async function reportChunks(entry, isAuto = false) {
    await chrome.runtime.sendMessage({
      type: 'report-chunks',
      messageKey: entry.messageKey,
      chunks: entry.chunks,
      autoPreview: entry.autoPreview,
      isAuto,
      voiceProfile: getCurrentVoiceProfile(),
      ...getSpeakParams(),
      title: getPlainDocumentTitle(),
    }).catch(() => {});
  }

  async function reportLatestAssistantSnapshot() {
    const nodes = getAssistantNodes();
    if (!nodes.length) return false;
    const node = nodes[nodes.length - 1];
    const text = extractAssistantText(node);
    if (!text) return false;
    const item = ensureElementState(node, text);
    const chunks = splitSpeakChunks(text, {
      maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
      maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
    const preview = extractAutoPreview(text, {
      maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
      maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
    if (!chunks.length || !preview) return false;
    await reportChunks({
      node,
      text,
      messageKey: item.key,
      chunks,
      autoPreview: preview,
      capturedAt: Date.now(),
    }, false);
    return true;
  }

  async function registerCurrentTab({ claimOwner = false, includeLatest = false } = {}) {
    const response = await runtimeMessage('register-tab', {
      title: getPlainDocumentTitle(),
      claimOwner: Boolean(claimOwner),
    });
    applyOwnerState(response && typeof response.isUiOwner !== 'undefined' ? response.isUiOwner : null, response || null);
    if (includeLatest) await reportLatestAssistantSnapshot();
    return response || null;
  }

  function maybeMarkResponseCompleted(node, item, text) {
    if (!item.sent || item.completionNotified) return;
    if (isResponseGenerating()) {
      item.generationObserved = true;
      if (item.completionTimer) clearTimeout(item.completionTimer);
      item.completionTimer = null;
      return;
    }
    const preview = extractAutoPreview(text, {
      maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
      maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
    if (!preview) return;
    const stableMs = stableDelayForPreview(preview);
    const requiredStableMs = item.generationObserved ? 0 : Math.max(stableMs, 1800);
    const remainingMs = requiredStableMs - (Date.now() - item.lastChangedAt);
    if (remainingMs > 0) {
      if (item.completionTimer) clearTimeout(item.completionTimer);
      item.completionTimer = setTimeout(() => {
        item.completionTimer = null;
        const latest = extractAssistantText(node);
        if (!latest) return;
        if (latest !== item.lastText) {
          processNode(node);
          return;
        }
        maybeMarkResponseCompleted(node, item, latest);
      }, remainingMs + 50);
      return;
    }
    item.completionNotified = true;
    if (item.completionTimer) clearTimeout(item.completionTimer);
    item.completionTimer = null;
    void markResponseCompleted();
  }

  function processNode(node) {
    const text = extractAssistantText(node);
    if (!text) return;
    const item = ensureElementState(node, text);
    if (isResponseGenerating()) item.generationObserved = true;
    if (item.sent) {
      if (text === item.lastText) {
        maybeMarkResponseCompleted(node, item, text);
        return;
      }
      item.lastText = text;
      item.lastChangedAt = Date.now();
      const chunks = splitSpeakChunks(text, {
        maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
        maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
        minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
      });
      const preview = extractAutoPreview(text, {
        maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
        maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
        minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
      });
      if (chunks.length && preview) {
        void reportChunks({
          node,
          text,
          messageKey: item.key,
          chunks,
          autoPreview: preview,
          capturedAt: Date.now(),
        }, false);
        maybeMarkResponseCompleted(node, item, text);
      }
      return;
    }
    if (!initializedElements.has(node)) {
      initializedElements.add(node);
      item.lastText = text;
      item.lastChangedAt = Date.now();
      if (!enabled || !settings.enabled) return;
    }
    if (text !== item.lastText) {
      item.lastText = text;
      item.lastChangedAt = Date.now();
    }
    const chunks = splitSpeakChunks(text, {
      maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
      maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
    const preview = extractAutoPreview(text, {
      maxLines: Number(settings.previewMaxLines || DEFAULT_SETTINGS.previewMaxLines),
      maxChars: Number(settings.previewMaxChars || DEFAULT_SETTINGS.previewMaxChars),
      minChars: Number(settings.previewMinChars || DEFAULT_SETTINGS.previewMinChars),
    });
    if (!preview) return;
    const entry = { node, text, messageKey: item.key, chunks, autoPreview: preview, capturedAt: Date.now() };
    if (shouldSendNow(node, preview, Date.now(), item)) {
      item.sent = true;
      node.dataset[AUTO_SENT_FLAG] = '1';
      void reportChunks(entry, Boolean(enabled && settings.enabled));
      maybeMarkResponseCompleted(node, item, text);
      return;
    }
    schedulePendingAutoSend(node, item, preview);
  }

  function inspectLatestAssistant() {
    const nodes = getAssistantNodes();
    if (nodes.length === 0) return;
    processNode(nodes[nodes.length - 1]);
    if (settings.micConversationEnabled) void ensureLiveController()?.inspect();
  }

  function removedNodeContainsGenerationControl(node) {
    if (!node || node.nodeType !== 1) return false;
    return node.matches(RESPONSE_GENERATING_SELECTOR)
      || Boolean(node.querySelector(RESPONSE_GENERATING_SELECTOR));
  }

  function scheduleInspect(mutations = []) {
    const generationEnded = mutations.some((mutation) => Array.from(mutation.removedNodes || [])
      .some(removedNodeContainsGenerationControl));
    if (generationEnded) {
      if (inspectTimer) clearTimeout(inspectTimer);
      inspectTimer = null;
      inspectLatestAssistant();
      return;
    }
    if (inspectTimer) return;
    inspectTimer = setTimeout(() => {
      inspectTimer = null;
      inspectLatestAssistant();
    }, 200);
  }

  async function syncDesktopPetSelection(referenceVoice = getCurrentReferenceVoice()) {
    const petId = resolveDesktopPetId(referenceVoice);
    try {
      await runtimeMessage('desktop-pet-selection', { petId });
    } catch (_error) {}
  }

  function releaseObjectUrl() {
    if (currentObjectUrl) URL.revokeObjectURL(currentObjectUrl);
    currentObjectUrl = null;
  }

  function releaseSpecificObjectUrl(objectUrl) {
    if (!objectUrl) return;
    try { URL.revokeObjectURL(objectUrl); } catch (_error) {}
    if (currentObjectUrl === objectUrl) currentObjectUrl = null;
  }

  function playbackLeaseMs(durationSeconds) {
    const duration = Number(durationSeconds);
    if (!Number.isFinite(duration) || duration <= 0) return 90_000;
    return Math.max(30_000, Math.min(900_000, Math.ceil(duration * 1000) + 15_000));
  }

  function base64ToBlob(base64, contentType) {
    const binary = atob(String(base64 || ''));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return new Blob([bytes], { type: contentType || 'audio/wav' });
  }

  async function fetchAudioObjectUrl(url) {
    const payload = await runtimeMessage('fetch-audio', { url });
    if (!payload || !payload.base64) throw new Error('audio data is empty');
    const blob = base64ToBlob(payload.base64, payload.contentType || 'audio/wav');
    if (!blob || blob.size === 0) throw new Error('audio blob is empty');
    return URL.createObjectURL(blob);
  }


  async function playItem(url, text, item, playbackToken) {
    stopCurrentPlayback('replace');
    const token = String(playbackToken || '');
    currentPlaybackToken = token;
    let audioSrc = null;
    let playbackAudio = null;
    if (settings.micConversationEnabled) {
      reportConversationState({ phase: 'speaking', statusText: '読み上げ中', error: '', sttModel: settings.sttModel });
    }
    try {
      audioSrc = await fetchAudioObjectUrl(url);
      if (currentPlaybackToken !== token) {
        releaseSpecificObjectUrl(audioSrc);
        return;
      }
      releaseObjectUrl();
      currentObjectUrl = audioSrc;
      await new Promise((resolve, reject) => {
        const audio = new Audio(audioSrc);
        playbackAudio = audio;
        audio.volume = clampVolume(settings.voiceVolume);
        currentAudio = audio;
        let settled = false;
        let watchdogTimer = null;
        const cleanup = () => {
          if (watchdogTimer) clearTimeout(watchdogTimer);
          watchdogTimer = null;
          audio.onended = null;
          audio.onerror = null;
          audio.onabort = null;
        };
        const settle = (callback, value) => {
          if (settled) return;
          settled = true;
          cleanup();
          callback(value);
        };
        const armWatchdog = (durationSeconds) => {
          if (watchdogTimer) clearTimeout(watchdogTimer);
          watchdogTimer = setTimeout(() => {
            settle(reject, new Error('audio playback timed out'));
          }, playbackLeaseMs(durationSeconds));
        };
        currentPlaybackCancel = () => {
          const stopped = new Error('playback stopped');
          stopped.code = 'PLAYBACK_STOPPED';
          settle(reject, stopped);
        };
        audio.onended = () => settle(resolve);
        audio.onerror = () => settle(reject, new Error('audio element failed'));
        audio.onabort = () => settle(reject, new Error('audio playback aborted'));
        armWatchdog(0);
        audio.play().then(() => {
          if (settled || currentPlaybackToken !== token) return;
          const durationSeconds = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
          armWatchdog(durationSeconds);
          chrome.runtime.sendMessage({ type: 'playback-started', playbackToken: token, durationSeconds }).catch(() => {});
        }).catch((error) => settle(reject, error));
      });
      if (currentPlaybackToken !== token) return;
      releaseSpecificObjectUrl(audioSrc);
      currentAudio = null;
      currentPlaybackCancel = null;
      currentPlaybackToken = null;
      chrome.runtime.sendMessage({ type: 'playback-done', playbackToken: token, ok: true, stopped: false }).catch(() => {});
      if (settings.micConversationEnabled && currentConversationPhase === 'speaking') {
        reportConversationState({ phase: 'idle', statusText: '待機中（右Ctrl＋＼ 長押し）', error: '', sttModel: settings.sttModel });
      }
    } catch (error) {
      const stopped = error && error.code === 'PLAYBACK_STOPPED';
      const stale = currentPlaybackToken !== token;
      releaseSpecificObjectUrl(audioSrc);
      if (currentAudio === playbackAudio) currentAudio = null;
      if (!stale) currentPlaybackCancel = null;
      if (stale || stopped) return;
      currentPlaybackToken = null;
      chrome.runtime.sendMessage({ type: 'playback-done', playbackToken: token, ok: false, stopped: false, error: error.message || String(error) }).catch(() => {});
      if (settings.micConversationEnabled && currentConversationPhase === 'speaking') {
        reportConversationState({ phase: 'error', statusText: '読み上げに失敗しました', error: error.message || String(error), sttModel: settings.sttModel });
      }
    }
  }

  function stopCurrentPlayback(reason = 'stop') {
    const playbackId = currentPlaybackToken;
    if (currentAudio) {
      try { currentAudio.pause(); currentAudio.currentTime = 0; } catch (_error) {}
    }
    if (currentPlaybackCancel) currentPlaybackCancel();
    currentAudio = null;
    currentPlaybackCancel = null;
    currentPlaybackToken = null;
    releaseObjectUrl();
    return playbackId;
  }

  function applyOwnerState(nextIsOwner) {
    isUiOwner = nextIsOwner;
  }

  function hideCancelOverlay() {
    if (cancelOverlay) cancelOverlay.remove();
    cancelOverlay = null;
  }

  function showCancelOverlay(graceMs) {
    hideCancelOverlay();
    cancelOverlay = document.createElement('div');
    cancelOverlay.id = 'local-voice-cancel-hint';
    cancelOverlay.textContent = `Escでキャンセル · ${(Math.max(0, Number(graceMs) || 0) / 1000).toFixed(1)}秒`;
    Object.assign(cancelOverlay.style, {
      position: 'fixed',
      right: '18px',
      bottom: '18px',
      zIndex: '2147483647',
      padding: '7px 10px',
      borderRadius: '8px',
      background: 'rgba(16, 18, 24, 0.88)',
      color: '#f7f8fb',
      font: '12px system-ui, sans-serif',
      pointerEvents: 'none',
      boxShadow: '0 4px 18px rgba(0, 0, 0, 0.25)',
    });
    document.documentElement.appendChild(cancelOverlay);
  }

  function reportConversationState(payload) {
    currentConversationPhase = String(payload && payload.phase || 'error');
    if (!globalThis.chrome || !chrome.runtime || !chrome.runtime.sendMessage) return;
    chrome.runtime.sendMessage({ type: 'conversation-state', payload }).catch(() => {});
  }

  function reportComposerFocus(target = document.activeElement) {
    const api = globalThis.LocalVoicePromptInput;
    if (!api || typeof api.findComposer !== 'function' || !target) return;
    const composer = api.findComposer(document, target);
    if (!composer) return;
    if (typeof api.isComposerTarget === 'function' ? !api.isComposerTarget(document, target) : (target !== composer && !(typeof composer.contains === 'function' && composer.contains(target)))) return;
    chrome.runtime.sendMessage({ type: 'composer-focused', title: getPlainDocumentTitle() }).catch(() => {});
  }

  function conversationTargetStatus() {
    const api = globalThis.LocalVoicePromptInput;
    if (!api || typeof api.findComposer !== 'function') return { ok: false, reason: 'prompt-input-core-unavailable' };
    const activeElement = document.activeElement || null;
    const composer = api.findComposer(document, activeElement);
    const composerFocused = Boolean(composer && activeElement && (typeof api.isComposerTarget === 'function'
      ? api.isComposerTarget(document, activeElement)
      : activeElement === composer || (typeof composer.contains === 'function' && composer.contains(activeElement))));
    const documentFocused = typeof document.hasFocus === 'function' ? document.hasFocus() : true;
    const visible = document.visibilityState !== 'hidden';
    return {
      ok: true,
      composerAvailable: Boolean(composer),
      composerFocused: Boolean(composerFocused && documentFocused && visible),
      documentFocused: Boolean(documentFocused),
      visible,
      url: location.href,
    };
  }

  function appliedDeliveryLedger() {
    if (deliveryLedger) return deliveryLedger;
    const api = globalThis.LocalVoiceDeliveryIds;
    if (!api || typeof api.createLedger !== 'function') return null;
    deliveryLedger = api.createLedger(sessionStorage, {
      key: 'localVoiceAppliedDeliveryIds',
      limit: 128,
    });
    return deliveryLedger;
  }

  function ensurePendingSendController() {
    if (pendingSendController) return pendingSendController;
    const api = globalThis.LocalVoicePromptInput;
    if (!api || typeof api.createPendingSendController !== 'function') return null;
    const live = ensureLiveController();
    if (!live) return null;
    pendingSendController = api.createPendingSendController({
      document,
      window,
      Event,
      InputEvent,
      getLocation: () => location.href,
      prepareSubmission: (item) => live.prepareSubmission(item),
      commitSubmission: (item) => live.commitSubmission(item),
      invalidateSubmission: (item, reason) => live.invalidateSubmission(item, reason),
      markSubmissionClick: (item, composer) => live.markSubmissionClick(item, composer),
      onState: (state) => {
        if (state.phase === 'pending_send') showCancelOverlay(settings.cancelGraceMs);
        else hideCancelOverlay();
        reportConversationState({
          phase: state.phase,
          statusText: state.statusText,
          error: state.error || '',
          sttModel: settings.sttModel || 'small',
        });
      },
    });
    return pendingSendController;
  }

  async function loadSettings() {
    if (!globalThis.chrome || !chrome.storage || !chrome.storage.local) {
      settings = { ...DEFAULT_SETTINGS };
      enabled = false;
      return;
    }
    const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
    settings = await sanitizeStoredSettings(stored);
    settings.voiceVolume = clampVolume(settings.voiceVolume);
    enabled = Boolean(settings.enabled);
  }

  function registerMessageListener() {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (!message || typeof message.type !== 'string') return false;
      if (message.type === 'conversation-target-status') {
        sendResponse(conversationTargetStatus());
        return false;
      }
      if (message.type === 'tab-activated') {
        clearCompletionMarker();
        return false;
      }
      if (message.type === 'bridge-reconnect') {
        registerCurrentTab({ includeLatest: true })
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }
      if (message.type === 'state-update') {
        applyOwnerState(message.payload.isUiOwner, message.payload);
        return false;
      }
      if (message.type === 'play-audio') {
        void playItem(message.payload.url, message.payload.text, message.payload.item, String(message.payload.playbackToken || ''));
        return false;
      }
      if (message.type === 'voice-transcript') {
        if (!settings.micConversationEnabled) {
          sendResponse({ ok: false, reason: 'mic-conversation-disabled' });
          return false;
        }
        const controller = ensurePendingSendController();
        if (!controller) {
          reportConversationState({
            phase: 'error',
            statusText: '音声入力モジュールを読み込めませんでした',
            error: 'prompt-input-core-unavailable',
            sttModel: settings.sttModel,
          });
          sendResponse({ ok: false, reason: 'prompt-input-core-unavailable' });
          return false;
        }
        const payload = message.payload || {};
        const deliveryId = String(payload.deliveryId || '').trim();
        const ledger = appliedDeliveryLedger();
        if (deliveryId && ledger && ledger.has(deliveryId)) {
          sendResponse({ ok: true, alreadyApplied: true });
          return false;
        }
        settings.cancelGraceMs = Math.max(0, Math.min(5000, Number(payload.cancelGraceMs) || 0));
        const live = ensureLiveController();
        if (!live) {
          sendResponse({ ok: false, reason: 'live-content-controller-unavailable' });
          return false;
        }
        const text = String(payload.text || '');
        const metadata = live.metadata(String(payload.sessionId || ''), text);
        Promise.resolve(controller.start({
          ...metadata,
          text,
          graceMs: settings.cancelGraceMs,
        })).then((result) => {
          if (result && result.ok === true && deliveryId && ledger) ledger.mark(deliveryId);
          sendResponse(result);
        }).catch((error) => sendResponse({ ok: false, reason: error.message || String(error) }));
        return true;
      }
      if (message.type === 'cancel-voice-send') {
        const controller = ensurePendingSendController();
        const result = controller ? controller.cancel('new-recording') : { ok: false, reason: 'nothing-pending' };
        hideCancelOverlay();
        sendResponse(result);
        return false;
      }
      if (message.type === 'stop-audio') {
        const incomingToken = String((message.payload && message.payload.playbackToken) || '');
        if (!incomingToken || incomingToken === currentPlaybackToken) stopCurrentPlayback('stop');
        sendResponse({ ok: true });
        return false;
      }
      return false;
    });
  }

  async function start() {
    registerMessageListener();
    document.getElementById('local-voice-pixel-pet')?.remove();
    document.getElementById('local-voice-bridge-panel')?.remove();
    await loadSettings();
    await syncDesktopPetSelection();
    baseDocumentTitle = stripCompletionTitlePrefix(document.title);
    try {
      completionMarkerPending = sessionStorage.getItem(COMPLETION_SESSION_KEY) === '1';
    } catch (_error) {
      completionMarkerPending = false;
    }
    if (await isTabActivelyViewed()) setCompletionMarkerPending(false);
    else syncCompletionMarker();
    titleObserver = new MutationObserver(syncCompletionMarker);
    titleObserver.observe(document.head, { childList: true, subtree: true, characterData: true });
    markExistingMessagesAsSeen();
    try {
      await registerCurrentTab({ includeLatest: true });
    } catch (_error) {
      applyOwnerState(null);
    }
    const live = ensureLiveController();
    if (settings.micConversationEnabled) void live?.reconcile();
    observer = new MutationObserver(scheduleInspect);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    const claimUiOwnership = () => {
      chrome.runtime.sendMessage({ type: 'register-tab', title: getPlainDocumentTitle(), claimOwner: true }).catch(() => {});
    };
    const pollExternalControl = () => {
      if (isUiOwner !== false) {
        chrome.runtime.sendMessage({ type: 'external-control-poll' }).catch(() => {});
      }
      const delay = settings.micConversationEnabled ? 50 : 750;
      window.setTimeout(pollExternalControl, delay);
    };
    window.addEventListener('focus', clearCompletionMarker);
    document.addEventListener('visibilitychange', () => {
      void isTabActivelyViewed().then((active) => {
        if (active) clearCompletionMarker();
      });
    });
    window.addEventListener('focus', claimUiOwnership);
    document.addEventListener('pointerdown', clearCompletionMarker, { capture: true });
    document.addEventListener('pointerdown', claimUiOwnership, { capture: true });
    document.addEventListener('focusin', (event) => reportComposerFocus(event.target), { capture: true });
    document.addEventListener('pointerdown', (event) => reportComposerFocus(event.target), { capture: true });
    document.addEventListener('input', (event) => {
      if (settings.micConversationEnabled) live?.handleInput(event);
    }, { capture: true });
    document.addEventListener('keydown', (event) => {
      if (!settings.micConversationEnabled) return;
      const promptApi = globalThis.LocalVoicePromptInput;
      live?.handleKeydown(event, (target) => Boolean(promptApi && promptApi.isComposerTarget(document, target)));
    }, { capture: true });
    document.addEventListener('pointerdown', (event) => {
      if (settings.micConversationEnabled) live?.handlePointer(event);
    }, { capture: true });
    reportComposerFocus();
    window.addEventListener('pagehide', () => {
      if (pendingSendController) pendingSendController.cancel('page-changed');
      void live?.interrupt('pagehide');
      hideCancelOverlay();
    });
    void pollExternalControl();
    setInterval(() => {
      chrome.runtime.sendMessage({ type: 'register-tab', title: getPlainDocumentTitle() }).catch(() => {});
    }, 5000);
  }

  if (globalThis.chrome && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      const wasEnabled = enabled;
      for (const [key, change] of Object.entries(changes)) settings[key] = change.newValue;
      enabled = Boolean(settings.enabled);
      if (!wasEnabled && enabled) rebaselineAutoMessages();
      settings.voiceVolume = clampVolume(settings.voiceVolume);
      if (Object.prototype.hasOwnProperty.call(changes, 'micConversationEnabled')) {
        if (!settings.micConversationEnabled) {
          if (pendingSendController) pendingSendController.cancel('disabled');
          void ensureLiveController()?.interrupt('disabled');
          hideCancelOverlay();
          reportConversationState({ phase: 'off', statusText: 'マイク会話オフ', error: '', sttModel: settings.sttModel });
        } else {
          void ensureLiveController()?.reconcile();
        }
      }
      if (Object.prototype.hasOwnProperty.call(changes, 'voiceId')
        || Object.prototype.hasOwnProperty.call(changes, 'referenceVoice')) {
        void syncDesktopPetSelection();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { void start(); }, { once: true });
  } else {
    void start();
  }
})();

