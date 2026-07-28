const settingsCore = globalThis.BackgroundSettingsCore;
if (!settingsCore) throw new Error('background-settings-core.js must be loaded before background.js');
const {
  DEFAULT_SETTINGS,
  LEGACY_BROWSER_UI_STORAGE_KEYS,
  SETTINGS_VERSION,
  normalizeStoredReference,
  sanitizeSettings,
} = settingsCore;
const CHATGPT_TAB_PATTERNS = ['https://chatgpt.com/*', 'https://chat.openai.com/*'];
const BRIDGE_CONSUMER_ID_KEY = 'bridgeConsumerId';

const tabs = new Map();
const reconnectingTabs = new Map();
let selectedTabId = null;
let uiOwnerTabId = null;
let queue = [];
let isPlaying = false;
let playbackPhase = 'idle';
let currentItem = null;
let currentToken = null;
let currentPlaybackTabId = null;
let currentPlaybackDeadlineAt = 0;
let playbackWatchdogTimer = null;
let lastPlayedItem = null;
let seq = 1;
let lastStatusText = 'Ready';
let lastStatusLevel = 'info';
let lastKnownReferenceVoice = '';
let referenceSettingsLoaded = false;
let lastComposerFocusedTabId = null;
let activeConversationTargetTabId = null;
let conversationPhase = 'off';
const conversationSessionTargets = new Map();
const conversationSessionTargetLocations = new Map();
let browserRuntimeHydrated = false;
let browserRuntimeHydrationPromise = null;
let browserRuntimePersistTimer = null;
let browserRuntimePersistPromise = null;
let browserRuntimePersistDirty = false;
let backgroundInitializationPromise = null;

function rememberReferenceVoice(settings) {
  const safe = settings && typeof settings === 'object' ? settings : {};
  lastKnownReferenceVoice = normalizeStoredReference(safe.referenceVoice ?? safe.voiceId);
  referenceSettingsLoaded = true;
  return settings;
}

async function migrateSettings() {
  const current = await chrome.storage.local.get(null);
  const sanitized = sanitizeSettings(current);
  await chrome.storage.local.set(sanitized);
  await chrome.storage.local.remove(LEGACY_BROWSER_UI_STORAGE_KEYS);
  rememberReferenceVoice(sanitized);
}

async function getSettings() {
  const stored = await chrome.storage.local.get(DEFAULT_SETTINGS);
  const sanitized = sanitizeSettings(stored);
  if (stored.voiceProfile !== sanitized.voiceProfile || stored.referenceVoice !== sanitized.referenceVoice || stored.settingsVersion !== SETTINGS_VERSION) {
    await chrome.storage.local.set(sanitized);
  }
  return rememberReferenceVoice(sanitized);
}

function cloneItem(item) {
  return globalThis.BackgroundRuntimeCore.cloneItem(item);
}

function browserRuntimeStatePayload() {
  return globalThis.BackgroundRuntimeCore.createPayload({
    tabs,
    selectedTabId,
    uiOwnerTabId,
    lastComposerFocusedTabId,
    activeConversationTargetTabId,
    conversationSessionTargets,
    conversationSessionTargetLocations,
    queue,
    currentItem,
    lastPlayedItem,
    seq,
  });
}

function applyBrowserRuntimeSnapshot(value) {
  const merged = globalThis.BackgroundRuntimeCore.mergeSnapshot(value, {
    tabs,
    selectedTabId,
    uiOwnerTabId,
    lastComposerFocusedTabId,
    activeConversationTargetTabId,
    conversationSessionTargets,
    conversationSessionTargetLocations,
    queue,
    currentItem,
    isPlaying,
    lastPlayedItem,
    seq,
  });
  tabs.clear();
  for (const [tabId, info] of merged.tabs.entries()) tabs.set(tabId, info);
  conversationSessionTargets.clear();
  conversationSessionTargetLocations.clear();
  for (const [sessionId, tabId] of merged.conversationSessionTargets.entries()) {
    conversationSessionTargets.set(sessionId, tabId);
    conversationSessionTargetLocations.set(
      sessionId,
      String(merged.conversationSessionTargetLocations.get(sessionId) || ''),
    );
  }
  selectedTabId = merged.selectedTabId;
  uiOwnerTabId = merged.uiOwnerTabId;
  lastComposerFocusedTabId = merged.lastComposerFocusedTabId;
  activeConversationTargetTabId = merged.activeConversationTargetTabId;
  queue = merged.queue;
  if (merged.resetPlayback) {
    currentItem = null;
    currentToken = null;
    currentPlaybackTabId = null;
    currentPlaybackDeadlineAt = 0;
    isPlaying = false;
    playbackPhase = 'idle';
  }
  lastPlayedItem = merged.lastPlayedItem;
  seq = merged.seq;
  ensureOwner();
  browserRuntimeHydrated = true;
  scheduleBrowserRuntimePersist();
  return true;
}

async function hydrateBrowserRuntime() {
  if (browserRuntimeHydrated) return true;
  if (browserRuntimeHydrationPromise) return browserRuntimeHydrationPromise;
  browserRuntimeHydrationPromise = (async () => {
    try {
      const settings = await getSettings();
      const payload = await controlPanelRequest(settings, '/v1/browser-runtime');
      if (payload.browserRuntime && payload.browserRuntime.currentItem) {
        await stopLocalAudio().catch(() => {});
      }
      applyBrowserRuntimeSnapshot(payload.browserRuntime);
      if (queue.length && !isPlaying) void playNext();
      return true;
    } catch (_error) {
      browserRuntimeHydrated = false;
      return false;
    }
  })();
  try {
    return await browserRuntimeHydrationPromise;
  } finally {
    browserRuntimeHydrationPromise = null;
  }
}

async function flushBrowserRuntimeState() {
  if (!browserRuntimeHydrated) return;
  browserRuntimePersistDirty = true;
  if (browserRuntimePersistPromise) return browserRuntimePersistPromise;
  browserRuntimePersistPromise = (async () => {
    while (browserRuntimePersistDirty) {
      browserRuntimePersistDirty = false;
      const settings = await getSettings();
      await controlPanelRequest(settings, '/v1/browser-runtime', {
        method: 'POST',
        body: browserRuntimeStatePayload(),
      });
    }
  })();
  try {
    await browserRuntimePersistPromise;
  } finally {
    browserRuntimePersistPromise = null;
  }
}

function scheduleBrowserRuntimePersist() {
  if (!browserRuntimeHydrated) return;
  browserRuntimePersistDirty = true;
  if (browserRuntimePersistTimer) return;
  browserRuntimePersistTimer = setTimeout(() => {
    browserRuntimePersistTimer = null;
    void flushBrowserRuntimeState().catch(() => {});
  }, 25);
  if (browserRuntimePersistTimer && typeof browserRuntimePersistTimer.unref === 'function') {
    browserRuntimePersistTimer.unref();
  }
}

function setStatus(text, level = 'info') {
  lastStatusText = String(text || 'Ready');
  lastStatusLevel = String(level || 'info');
}

function ensureOwner() {
  const ids = Array.from(tabs.keys());
  if (!ids.length) {
    uiOwnerTabId = null;
    selectedTabId = null;
    lastComposerFocusedTabId = null;
    activeConversationTargetTabId = null;
    conversationSessionTargets.clear();
    conversationSessionTargetLocations.clear();
    return;
  }
  if (!uiOwnerTabId || !tabs.has(uiOwnerTabId)) uiOwnerTabId = ids[0];
  if (!selectedTabId || !tabs.has(selectedTabId)) selectedTabId = uiOwnerTabId;
  if (lastComposerFocusedTabId && !tabs.has(lastComposerFocusedTabId)) lastComposerFocusedTabId = null;
  if (activeConversationTargetTabId && !tabs.has(activeConversationTargetTabId)) activeConversationTargetTabId = null;
}

function preferredConversationTarget() {
  ensureOwner();
  if (lastComposerFocusedTabId && tabs.has(lastComposerFocusedTabId)) return lastComposerFocusedTabId;
  if (selectedTabId && tabs.has(selectedTabId)) return selectedTabId;
  return uiOwnerTabId && tabs.has(uiOwnerTabId) ? uiOwnerTabId : null;
}

async function conversationTargetStatus(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'conversation-target-status' });
    if (!response || response.ok !== true) return { tabId, ok: false, composerAvailable: false, composerFocused: false };
    return {
      tabId,
      ok: true,
      composerAvailable: Boolean(response.composerAvailable),
      composerFocused: Boolean(response.composerFocused),
      documentFocused: Boolean(response.documentFocused),
      visible: Boolean(response.visible),
      url: String(response.url || ''),
    };
  } catch (_error) {
    return { tabId, ok: false, composerAvailable: false, composerFocused: false };
  }
}

async function captureConversationTarget() {
  ensureOwner();
  const tabIds = Array.from(tabs.keys());
  if (!tabIds.length) return null;
  const statuses = await Promise.all(tabIds.map((tabId) => conversationTargetStatus(tabId)));
  const byTabId = new Map(statuses.map((status) => [status.tabId, status]));
  const focused = statuses.filter((status) => status.ok && status.composerFocused);
  if (focused.length) {
    const preferredFocused = [lastComposerFocusedTabId, selectedTabId, uiOwnerTabId]
      .map((tabId) => focused.find((status) => status.tabId === tabId))
      .find(Boolean);
    return preferredFocused || focused[0];
  }
  const candidates = [lastComposerFocusedTabId, selectedTabId, uiOwnerTabId, ...tabIds];
  for (const tabId of candidates) {
    const status = byTabId.get(tabId);
    if (status && status.ok && status.composerAvailable) return status;
  }
  return null;
}

function conversationLocationKey(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  try {
    const url = new URL(raw);
    return `${url.origin}${url.pathname}`;
  } catch (_error) {
    return raw.split(/[?#]/, 1)[0];
  }
}

function retryableTranscriptFailure(reason) {
  return ['composer-not-found', 'composer-state-not-updated', 'prompt-input-core-unavailable', 'message-delivery-failed']
    .includes(String(reason || ''));
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function deliverVoiceTranscript(targetTabId, payload, settings) {
  let lastReason = 'message-delivery-failed';
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await chrome.tabs.sendMessage(targetTabId, { type: 'voice-transcript', payload });
      if (response && response.ok === true) {
        return { ok: true, alreadyApplied: Boolean(response.alreadyApplied) };
      }
      lastReason = String(response && (response.reason || response.error) || 'message-delivery-failed');
    } catch (_error) {
      lastReason = 'message-delivery-failed';
    }
    if (!retryableTranscriptFailure(lastReason) || attempt === 2) break;
    await delay(50 * (attempt + 1));
  }
  await postConversationState({
    phase: 'error',
    statusText: '音声入力をChatGPT入力欄へ反映できませんでした',
    sttModel: settings.sttModel || 'small',
    error: lastReason,
  }).catch(() => {});
  return { ok: false, reason: lastReason };
}

function shouldQueueAutoFromTab(tabId) {
  if (['recording', 'preparing_model', 'transcribing', 'pending_send', 'sending'].includes(conversationPhase)) {
    return false;
  }
  if (conversationPhase === 'waiting_response' && activeConversationTargetTabId) {
    return Number(tabId) === Number(activeConversationTargetTabId);
  }
  return true;
}

function preserveReadChunkBoundary(previousMessage, incomingChunks, lastReadIndex) {
  if (!previousMessage || !Array.isArray(previousMessage.chunks) || lastReadIndex < 0) return incomingChunks;
  const consumed = previousMessage.chunks
    .slice(0, lastReadIndex + 1)
    .map((value) => String(value || '').trim())
    .filter(Boolean);
  if (!consumed.length) return incomingChunks;
  let prefix = consumed.join(' ');
  const continuation = [];
  for (const rawChunk of incomingChunks) {
    const chunk = String(rawChunk || '').trim();
    if (!chunk) continue;
    if (!prefix) {
      continuation.push(chunk);
      continue;
    }
    if (prefix === chunk) {
      prefix = '';
      continue;
    }
    if (prefix.startsWith(`${chunk} `)) {
      prefix = prefix.slice(chunk.length + 1).trim();
      continue;
    }
    if (chunk.startsWith(prefix)) {
      const remainder = chunk.slice(prefix.length).trim();
      prefix = '';
      if (remainder) continuation.push(remainder);
      continue;
    }
    return incomingChunks;
  }
  if (prefix) return incomingChunks;
  return [...consumed, ...continuation];
}

function statePayload(forTabId = null) {
  ensureOwner();
  return {
    isUiOwner: forTabId ? forTabId === uiOwnerTabId : undefined,
    uiOwnerTabId,
    selectedTabId,
    tabs: Array.from(tabs.entries()).map(([id, info]) => ({ id, title: info.title, url: info.url })),
    queueSize: queue.length,
    isPlaying,
    playbackPhase,
    currentPlayingItem: cloneItem(currentItem),
    lastPlayedItem: cloneItem(lastPlayedItem),
    replayAvailable: Boolean(lastPlayedItem && lastPlayedItem.audioUrl),
    statusText: lastStatusText,
    statusLevel: lastStatusLevel,
  };
}

function broadcastState() {
  scheduleBrowserRuntimePersist();
  for (const tabId of tabs.keys()) {
    chrome.tabs.sendMessage(tabId, { type: 'state-update', payload: statePayload(tabId) }).catch(() => {});
  }
}


function normalizeReferenceVoice(value) {
  const normalized = String(value ?? '').trim();
  if (!normalized || ['none', 'qwen3', 'qwen'].includes(normalized.toLowerCase())) return '';
  return normalized;
}

async function speak(text, requestId, voiceProfile, referenceVoice, voicePrompt) {
  const settings = await getSettings();
  const pickedProfile = DEFAULT_SETTINGS.voiceProfile;
  const pickedReferenceVoice = normalizeStoredReference(referenceVoice !== undefined ? referenceVoice : settings.referenceVoice);
  const pickedVoicePrompt = '';
  const response = await fetch(settings.apiUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      requestId,
      source: 'chatgpt-web',
      model: pickedProfile,
      voiceProfile: pickedProfile,
      voiceId: pickedReferenceVoice,
      referenceVoice: pickedReferenceVoice,
      voicePrompt: pickedVoicePrompt,
      instruct: pickedVoicePrompt,
      voiceVolume: Number(settings.voiceVolume),
      playLocal: true,
    }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.ok) throw new Error(body.error || `HTTP ${response.status}`);
  const returnedReferenceVoice = normalizeStoredReference(body.referenceVoice ?? body.voiceId);
  const usedReferenceAudio = String(body.usedReferenceAudio || '').trim();
  if (pickedReferenceVoice && (returnedReferenceVoice !== pickedReferenceVoice || !usedReferenceAudio)) {
    throw new Error(`Reference voice was not applied: ${pickedReferenceVoice}`);
  }
  return body;
}

function isAllowedAudioUrl(targetUrl, settings) {
  try {
    const target = new URL(String(targetUrl || ''));
    if (!target.pathname.startsWith('/audio/')) return false;
    const allowedHosts = new Set(['127.0.0.1', 'localhost']);
    if (!allowedHosts.has(target.hostname)) return false;
    const candidates = [settings.apiUrl, settings.healthUrl]
      .map((value) => {
        try {
          return new URL(String(value || ''));
        } catch (_error) {
          return null;
        }
      })
      .filter(Boolean);
    return candidates.some((candidate) => allowedHosts.has(candidate.hostname)
      && candidate.protocol === target.protocol
      && candidate.port === target.port);
  } catch (_error) {
    return false;
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    const chunk = bytes.subarray(i, i + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

async function fetchAudioPayload(url) {
  const targetUrl = String(url || '');
  const settings = await getSettings();
  if (!isAllowedAudioUrl(targetUrl, settings)) {
    throw new Error('unsupported audio URL');
  }
  const cacheBustedUrl = `${targetUrl}${targetUrl.includes('?') ? '&' : '?'}t=${Date.now()}`;
  const response = await fetch(cacheBustedUrl, { cache: 'no-store' });
  if (!response.ok) throw new Error(`audio fetch failed: ${response.status}`);
  const contentType = response.headers.get('Content-Type') || 'audio/wav';
  const buffer = await response.arrayBuffer();
  if (!buffer || buffer.byteLength === 0) throw new Error('audio blob is empty');
  return { base64: arrayBufferToBase64(buffer), contentType, size: buffer.byteLength };
}

async function fetchReferenceVoices() {
  const settings = await getSettings();
  const candidates = [];
  try {
    const healthUrl = new URL(settings.healthUrl || DEFAULT_SETTINGS.healthUrl);
    const refUrl = new URL(healthUrl.toString());
    refUrl.pathname = '/v1/reference-voices';
    refUrl.search = '';
    candidates.push(refUrl.toString(), healthUrl.toString());
  } catch (_error) {
    candidates.push('http://127.0.0.1:8717/v1/reference-voices', settings.healthUrl || DEFAULT_SETTINGS.healthUrl);
  }

  for (const url of candidates) {
    try {
      const response = await fetch(url, { cache: 'no-store' });
      const body = await response.json().catch(() => ({}));
      if (!response.ok || !body) continue;
      const voices = Array.isArray(body.voices) ? body.voices : Array.isArray(body.referenceVoices) ? body.referenceVoices : Array.isArray(body.availableReferenceVoices) ? body.availableReferenceVoices : [];
      return { ok: true, voices };
    } catch (_error) {}
  }
  return { ok: true, voices: [] };
}

async function syncDesktopPetSelection(petId) {
  const settings = await getSettings();
  const url = new URL(settings.healthUrl || DEFAULT_SETTINGS.healthUrl);
  url.pathname = '/v1/desktop-pet';
  url.search = '';
  const response = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ petId: String(petId || 'placeholder') }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function controlPanelUrl(settings, pathname, search = '') {
  const url = new URL(settings.healthUrl || DEFAULT_SETTINGS.healthUrl);
  url.pathname = pathname;
  url.search = search;
  url.hash = '';
  return url.toString();
}

async function controlPanelRequest(settings, pathname, options = {}) {
  const response = await fetch(controlPanelUrl(settings, pathname, options.search || ''), {
    method: options.method || 'GET',
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    body: options.body ? JSON.stringify(options.body) : undefined,
    cache: 'no-store',
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function replayLocalAudio(text = '') {
  const settings = await getSettings();
  return controlPanelRequest(settings, '/v1/playback/replay', {
    method: 'POST',
    body: {
      text: String(text || ''),
      voiceVolume: Number(settings.voiceVolume),
    },
  });
}

async function stopLocalAudio() {
  const settings = await getSettings();
  return controlPanelRequest(settings, '/v1/playback/stop', { method: 'POST', body: {} });
}

async function postConversationState(payload) {
  const settings = await getSettings();
  const safe = payload && typeof payload === 'object' ? payload : {};
  return controlPanelRequest(settings, '/v1/conversation/state', {
    method: 'POST',
    body: {
      phase: String(safe.phase || 'error'),
      statusText: String(safe.statusText || ''),
      sttDevice: String(safe.sttDevice || ''),
      sttModel: String(safe.sttModel || settings.sttModel || 'small'),
      error: String(safe.error || ''),
    },
  });
}

async function reconnectChatGptTab(tab) {
  const tabId = Number(tab && tab.id);
  if (!Number.isInteger(tabId) || tabId <= 0) return false;
  if (reconnectingTabs.has(tabId)) return reconnectingTabs.get(tabId);
  const attempt = (async () => {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: 'bridge-reconnect' });
      return Boolean(response && response.ok === true);
    } catch (_error) {}
    if (!chrome.scripting || typeof chrome.scripting.executeScript !== 'function') return false;
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        files: ['prompt-input-core.js', 'delivery-id-core.js', 'content-text-core.js', 'content.js'],
      });
      const response = await chrome.tabs.sendMessage(tabId, { type: 'bridge-reconnect' });
      return Boolean(response && response.ok === true);
    } catch (_error) {
      return false;
    }
  })();
  reconnectingTabs.set(tabId, attempt);
  try {
    return await attempt;
  } finally {
    reconnectingTabs.delete(tabId);
  }
}

async function reconnectOpenChatGptTabs() {
  let openTabs = [];
  try {
    openTabs = await chrome.tabs.query({ url: CHATGPT_TAB_PATTERNS });
  } catch (_error) {
    return;
  }
  const openTabIds = new Set(openTabs.map((tab) => Number(tab && tab.id)).filter((id) => Number.isInteger(id) && id > 0));
  for (const tabId of Array.from(tabs.keys())) {
    if (!openTabIds.has(tabId)) tabs.delete(tabId);
  }
  ensureOwner();
  await Promise.all(openTabs.map((tab) => reconnectChatGptTab(tab)));
  broadcastState();
}

function externalStateSnapshot() {
  const currentText = String(currentItem?.text || lastPlayedItem?.text || '');
  return {
    statusText: lastStatusText,
    statusLevel: lastStatusLevel,
    currentText,
    queueSize: queue.length,
    isPlaying,
    playbackPhase,
    replayAvailable: Boolean(lastPlayedItem && lastPlayedItem.audioUrl),
    tabsCount: tabs.size,
    loadedVersion: chrome.runtime.getManifest().version,
  };
}

const externalControlSync = globalThis.BackgroundControlSync.create({
  chrome,
  crypto,
  settingsCore,
  consumerIdStorageKey: BRIDGE_CONSUMER_ID_KEY,
  tabs,
  conversationSessionTargets,
  conversationSessionTargetLocations,
  getSettings,
  rememberReferenceVoice,
  controlPanelRequest,
  syncDesktopPetSelection,
  hydrateBrowserRuntime,
  recoverExpiredPlayback,
  executeUiCommand,
  flushBrowserRuntimeState,
  captureConversationTarget,
  conversationLocationKey,
  conversationTargetStatus,
  deliverVoiceTranscript,
  postConversationState,
  ensureOwner,
  reconnectOpenChatGptTabs,
  externalStateSnapshot,
  getConversationPhase: () => conversationPhase,
  setConversationPhase: (value) => { conversationPhase = value; },
  getActiveConversationTargetTabId: () => activeConversationTargetTabId,
  setActiveConversationTargetTabId: (value) => { activeConversationTargetTabId = value; },
});

async function pushOptionSettings(settings = null) {
  return externalControlSync.pushOptionSettings(settings);
}

async function syncExternalControlPanel() {
  return externalControlSync.synchronize();
}

function enqueue(base, front = false) {
  const item = {
    id: `q-${Date.now()}-${seq++}`,
    mode: String(base.mode || 'manual'),
    reason: String(base.reason || 'manual'),
    tabId: Number(base.tabId),
    tabTitle: String(base.tabTitle || 'ChatGPT'),
    messageKey: String(base.messageKey || ''),
    chunkIndex: Number(base.chunkIndex || 0),
    chunkCount: Number(base.chunkCount || 0),
    text: String(base.text || ''),
    voiceProfile: DEFAULT_SETTINGS.voiceProfile,
    referenceVoice: base.referenceVoice === undefined
      ? (referenceSettingsLoaded ? lastKnownReferenceVoice : undefined)
      : normalizeStoredReference(base.referenceVoice),
    voicePrompt: '',
    audioUrl: base.audioUrl ? String(base.audioUrl) : null,
  };
  if (front) queue.unshift(item);
  else queue.push(item);
  return item;
}

function chunkLabel(item) {
  const index = Math.max(0, Number(item?.chunkIndex || 0)) + 1;
  const count = Math.max(0, Number(item?.chunkCount || 0));
  return count > 0 ? `${index}/${count}` : String(index);
}

function playbackLeaseMs(durationSeconds) {
  const duration = Number(durationSeconds);
  if (!Number.isFinite(duration) || duration <= 0) return 90_000;
  return Math.max(30_000, Math.min(900_000, Math.ceil(duration * 1000) + 15_000));
}

function clearPlaybackWatchdog() {
  if (playbackWatchdogTimer) clearTimeout(playbackWatchdogTimer);
  playbackWatchdogTimer = null;
  currentPlaybackDeadlineAt = 0;
}

function clearCurrentPlayback() {
  clearPlaybackWatchdog();
  isPlaying = false;
  playbackPhase = 'idle';
  currentItem = null;
  currentToken = null;
  currentPlaybackTabId = null;
}

function armPlaybackWatchdog(timeoutMs) {
  clearPlaybackWatchdog();
  const safeTimeout = Math.max(1000, Number(timeoutMs) || playbackLeaseMs(0));
  currentPlaybackDeadlineAt = Date.now() + safeTimeout;
  playbackWatchdogTimer = setTimeout(() => {
    playbackWatchdogTimer = null;
    recoverExpiredPlayback(Date.now());
  }, safeTimeout + 50);
  if (playbackWatchdogTimer && typeof playbackWatchdogTimer.unref === 'function') playbackWatchdogTimer.unref();
}

function abandonCurrentPlayback(reason, level = 'warn') {
  if (!isPlaying) return false;
  const done = currentItem;
  const playbackToken = currentToken;
  const playbackTabId = currentPlaybackTabId;
  clearCurrentPlayback();
  void stopLocalAudio().catch(() => {});
  if (playbackTabId) {
    chrome.tabs.sendMessage(playbackTabId, { type: 'stop-audio', payload: { playbackToken } }).catch(() => {});
  }
  setStatus(`${reason} chunk ${chunkLabel(done)}`, level);
  broadcastState();
  void playNext();
  return true;
}

function recoverExpiredPlayback(now = Date.now()) {
  if (!isPlaying || playbackPhase !== 'playing' || !currentPlaybackDeadlineAt) return false;
  if (Number(now) < currentPlaybackDeadlineAt) return false;
  return abandonCurrentPlayback('Playback timed out; skipped', 'warn');
}

function selectedTarget(senderTabId) {
  if (senderTabId && tabs.has(senderTabId)) return senderTabId;
  if (selectedTabId && tabs.has(selectedTabId)) return selectedTabId;
  return Array.from(tabs.keys())[0] || null;
}

function queueCommand(cmd, senderTabId, _params = {}) {
  const tabId = selectedTarget(senderTabId);
  if (!tabId || !tabs.has(tabId)) {
    setStatus('No ChatGPT tab selected', 'warn');
    return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
  }
  const info = tabs.get(tabId);
  const message = info.lastAssistantMessage;
  if (!message || !Array.isArray(message.chunks) || !message.chunks.length) {
    setStatus('No assistant response yet', 'warn');
    return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
  }
  const lastReadIndex = Number.isInteger(info.lastReadIndex) ? info.lastReadIndex : -1;
  let chunkIndex = 0;
  if (cmd === 'next') {
    chunkIndex = lastReadIndex < 0 ? 0 : lastReadIndex + 1;
    if (chunkIndex >= message.chunks.length) {
      setStatus('End of response', 'info');
      return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
    }
  }
  if (cmd === 'regen') {
    chunkIndex = Math.min(message.chunks.length - 1, Math.max(0, lastReadIndex));
  }
  info.lastReadIndex = chunkIndex;
  const text = String(message.chunks[chunkIndex] || '').trim();
  if (!text) return { ok: true, payload: { statusText: 'Chunk text is empty', statusLevel: 'warn' } };
  enqueue({
    mode: cmd,
    reason: cmd,
    tabId,
    tabTitle: info.title,
    messageKey: message.messageKey,
    chunkIndex,
    chunkCount: message.chunks.length,
    text,
    voiceProfile: DEFAULT_SETTINGS.voiceProfile,
    referenceVoice: undefined,
    voicePrompt: '',
  });
  setStatus(`${cmd === 'regen' ? 'Regen' : 'Next'} chunk ${chunkIndex + 1}/${message.chunks.length}`, 'info');
  void playNext();
  return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
}

async function playNext() {
  if (isPlaying) return;
  ensureOwner();
  if (!uiOwnerTabId || !tabs.has(uiOwnerTabId)) return broadcastState();
  const item = queue.shift();
  if (!item) return broadcastState();
  isPlaying = true;
  playbackPhase = 'generating';
  currentItem = item;
  const playbackToken = crypto.randomUUID();
  currentToken = playbackToken;
  currentPlaybackTabId = null;
  setStatus(`Generating audio chunk ${chunkLabel(item)}`, 'info');
  broadcastState();
  try {
    await flushBrowserRuntimeState();
    let payload;
    if (item.audioUrl) {
      payload = await replayLocalAudio(item.text);
    } else {
      payload = await speak(item.text, `bg-${item.id}`, item.voiceProfile, item.referenceVoice, item.voicePrompt);
      item.audioUrl = payload.audioUrl;
      item.usedReferenceAudio = String(payload.usedReferenceAudio || '');
      item.voiceProfile = String(payload.voiceProfile || item.voiceProfile || '');
      item.referenceVoice = String(payload.referenceVoice || item.referenceVoice || '');
    }
    if (!isPlaying || currentToken !== playbackToken || currentItem !== item) return;
    if (payload && payload.playedLocally === true) {
      finishPlayback({
        playbackToken,
        ok: !payload.stopped && payload.playbackCompleted !== false,
        stopped: Boolean(payload.stopped),
        error: payload.error || '',
      });
      return;
    }

    // Compatibility with an older local API during an in-place upgrade.
    currentPlaybackTabId = uiOwnerTabId;
    playbackPhase = 'playing';
    setStatus(`Playing chunk ${chunkLabel(item)} · Ref ${item.referenceVoice || 'none'}`, 'info');
    armPlaybackWatchdog(playbackLeaseMs(0));
    broadcastState();
    await chrome.tabs.sendMessage(currentPlaybackTabId, {
      type: 'play-audio',
      payload: { url: item.audioUrl, text: item.text, playbackToken, item: cloneItem(item) },
    });
  } catch (error) {
    if (!isPlaying || currentToken !== playbackToken || currentItem !== item) return;
    clearCurrentPlayback();
    setStatus(`Playback failed: ${error.message || String(error)}`, 'error');
    broadcastState();
    void playNext();
  }
}

function finishPlayback(message) {
  const token = String((message && message.playbackToken) || '');
  if (!isPlaying || token !== currentToken) return { ok: true, payload: { ignored: true } };
  clearPlaybackWatchdog();
  currentPlaybackTabId = null;
  const done = currentItem;
  isPlaying = false;
  playbackPhase = 'idle';
  currentItem = null;
  currentToken = null;
  if (message.ok && !message.stopped) {
    lastPlayedItem = { ...cloneItem(done), playedAt: new Date().toISOString() };
    setStatus(`Played chunk ${chunkLabel(done)}`, 'info');
  } else {
    setStatus(message.stopped ? 'Playback stopped' : `Playback error: ${String(message.error || 'unknown')}`, message.stopped ? 'info' : 'error');
  }
  broadcastState();
  void playNext();
  return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
}

function executeUiCommand(cmd, senderTabId, params = {}) {
  const normalized = String(cmd || '').toLowerCase();
  if (normalized === 'stop' || normalized === 'skip') {
    queue = [];
    const playbackTabId = currentPlaybackTabId || uiOwnerTabId;
    const playbackToken = currentToken;
    void stopLocalAudio().catch(() => {});
    if (playbackTabId) chrome.tabs.sendMessage(playbackTabId, { type: 'stop-audio', payload: { playbackToken } }).catch(() => {});
    isPlaying = false;
    playbackPhase = 'idle';
    currentItem = null;
    currentToken = null;
    clearPlaybackWatchdog();
    currentPlaybackTabId = null;
    setStatus(normalized === 'skip' ? 'Skipped' : 'Stopped', 'info');
    broadcastState();
    if (normalized === 'skip') void playNext();
    return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
  }
  if (normalized === 'replay') {
    if (lastPlayedItem && lastPlayedItem.audioUrl) {
      enqueue({ ...lastPlayedItem, mode: 'replay', reason: 'replay' }, true);
      setStatus(`Replay chunk ${chunkLabel(lastPlayedItem)}`, 'info');
    } else {
      setStatus('No replay audio yet', 'warn');
    }
    void playNext();
    return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
  }
  if (normalized === 'next' || normalized === 'regen') {
    const result = queueCommand(normalized, senderTabId, params);
    broadcastState();
    return result;
  }
  setStatus(`Unsupported command: ${normalized}`, 'warn');
  return { ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } };
}

async function initializeBackgroundRuntime() {
  if (backgroundInitializationPromise) return backgroundInitializationPromise;
  backgroundInitializationPromise = (async () => {
    await migrateSettings();
    await hydrateBrowserRuntime();
    await reconnectOpenChatGptTabs();
    if (queue.length && !isPlaying) void playNext();
    return true;
  })();
  try {
    return await backgroundInitializationPromise;
  } finally {
    backgroundInitializationPromise = null;
  }
}

chrome.runtime.onInstalled.addListener(() => {
  void initializeBackgroundRuntime();
});
chrome.runtime.onStartup.addListener(() => {
  void initializeBackgroundRuntime();
});
void initializeBackgroundRuntime();
chrome.tabs.onRemoved.addListener((tabId) => {
  const ownedCurrentPlayback = isPlaying && currentPlaybackTabId === tabId;
  tabs.delete(tabId);
  queue = queue.filter((item) => item.tabId !== tabId);
  if (uiOwnerTabId === tabId) uiOwnerTabId = null;
  if (selectedTabId === tabId) selectedTabId = null;
  if (lastComposerFocusedTabId === tabId) lastComposerFocusedTabId = null;
  if (activeConversationTargetTabId === tabId) activeConversationTargetTabId = null;
  for (const [sessionId, targetTabId] of conversationSessionTargets.entries()) {
    if (targetTabId === tabId) conversationSessionTargets.set(sessionId, 0);
  }
  if (ownedCurrentPlayback) abandonCurrentPlayback('Playback tab closed; skipped', 'warn');
  else broadcastState();
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo || changeInfo.status !== 'loading') return;
  const ownedCurrentPlayback = isPlaying && currentPlaybackTabId === tabId;
  tabs.delete(tabId);
  if (uiOwnerTabId === tabId) uiOwnerTabId = null;
  if (selectedTabId === tabId) selectedTabId = null;
  if (lastComposerFocusedTabId === tabId) lastComposerFocusedTabId = null;
  if (activeConversationTargetTabId === tabId) activeConversationTargetTabId = null;
  for (const [sessionId, targetTabId] of conversationSessionTargets.entries()) {
    if (targetTabId === tabId) conversationSessionTargets.set(sessionId, 0);
  }
  if (ownedCurrentPlayback) abandonCurrentPlayback('Playback tab reloaded; skipped', 'warn');
  else broadcastState();
});
chrome.tabs.onActivated.addListener(({ tabId }) => {
  if (!tabs.has(tabId)) return;
  uiOwnerTabId = tabId;
  selectedTabId = tabId;
  chrome.tabs.sendMessage(tabId, { type: 'tab-activated' }).catch(() => {});
  broadcastState();
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string') return false;
  const senderTabId = sender.tab ? sender.tab.id : null;

  if (message.type === 'tab-attention-state') {
    if (!senderTabId) {
      sendResponse({ ok: true, payload: { active: false } });
      return false;
    }
    chrome.tabs.get(senderTabId)
      .then((tab) => sendResponse({ ok: true, payload: { active: Boolean(tab && tab.active) } }))
      .catch(() => sendResponse({ ok: true, payload: { active: false } }));
    return true;
  }

  if (message.type === 'register-tab') {
    if (senderTabId) {
      const existing = tabs.get(senderTabId) || { lastAssistantMessage: null, lastReadIndex: -1, lastAutoQueueSignature: '' };
      existing.title = String(message.title || sender.tab.title || 'ChatGPT');
      existing.url = sender.tab.url || existing.url || '';
      tabs.set(senderTabId, existing);
      if (message.claimOwner === true || (uiOwnerTabId == null && sender.tab.active)) {
        uiOwnerTabId = senderTabId;
        selectedTabId = senderTabId;
      }
    }
    sendResponse({ ok: true, payload: statePayload(senderTabId) });
    broadcastState();
    return false;
  }

  if (message.type === 'composer-focused') {
    if (senderTabId && tabs.has(senderTabId)) {
      lastComposerFocusedTabId = senderTabId;
      sendResponse({ ok: true, payload: { targetTabId: senderTabId } });
    } else {
      sendResponse({ ok: false, reason: 'tab-not-registered' });
    }
    return false;
  }

  if (message.type === 'report-chunks') {
    if (senderTabId && tabs.has(senderTabId)) {
      const info = tabs.get(senderTabId);
      const chunks = Array.isArray(message.chunks) ? message.chunks.map((x) => String(x || '').trim()).filter(Boolean) : [];
      const autoPreview = String(message.autoPreview || '').trim();
      const messageKey = String(message.messageKey || '').trim();
      if (messageKey && chunks.length) {
        const previousMessage = info.lastAssistantMessage;
        const sameMessage = Boolean(previousMessage && previousMessage.messageKey === messageKey);
        if (!sameMessage) info.lastReadIndex = -1;
        const updatedChunks = sameMessage
          ? preserveReadChunkBoundary(previousMessage, chunks, info.lastReadIndex)
          : chunks;
        info.lastAssistantMessage = { messageKey, chunks: updatedChunks, capturedAt: Date.now() };
        if (message.isAuto) {
          const autoText = autoPreview || chunks[0] || '';
          const autoQueueSignature = `${messageKey}\u0000${autoText}`;
          if (info.lastAutoQueueSignature !== autoQueueSignature) {
            info.lastAutoQueueSignature = autoQueueSignature;
            info.lastReadIndex = autoText ? 0 : -1;
            if (autoText && shouldQueueAutoFromTab(senderTabId)) {
              enqueue({ mode: 'auto', reason: 'auto', tabId: senderTabId, tabTitle: info.title, messageKey, chunkIndex: 0, chunkCount: chunks.length, text: autoText, voiceProfile: DEFAULT_SETTINGS.voiceProfile, referenceVoice: undefined, voicePrompt: '' });
              void playNext();
            } else if (autoText) {
              setStatus('音声入力中のため別の返答は読み上げませんでした', 'info');
            }
          }
        }
      }
    }
    sendResponse({ ok: true, payload: { statusText: lastStatusText, statusLevel: lastStatusLevel } });
    broadcastState();
    return false;
  }

  if (message.type === 'playback-started') {
    const token = String(message.playbackToken || '');
    if (isPlaying && token === currentToken && senderTabId === currentPlaybackTabId) {
      armPlaybackWatchdog(playbackLeaseMs(message.durationSeconds));
      sendResponse({ ok: true, payload: { accepted: true } });
    } else {
      sendResponse({ ok: true, payload: { ignored: true } });
    }
    return false;
  }

  if (message.type === 'playback-done') {
    sendResponse(finishPlayback(message));
    return false;
  }

  if (message.type === 'fetch-audio') {
    fetchAudioPayload(message.url)
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'reference-voices') {
    fetchReferenceVoices()
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'desktop-pet-selection') {
    syncDesktopPetSelection(message.petId)
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'options-settings-updated') {
    getSettings()
      .then((settings) => pushOptionSettings(settings))
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'external-control-poll') {
    syncExternalControlPanel()
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'conversation-state') {
    postConversationState(message.payload)
      .then((payload) => sendResponse({ ok: true, payload }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.type === 'ui-command') {
    const params = message.params && typeof message.params === 'object' ? message.params : {};
    const result = executeUiCommand(message.cmd, senderTabId, params);
    flushBrowserRuntimeState()
      .then(() => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }
  return false;
});

