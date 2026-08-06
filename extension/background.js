const settingsCore = globalThis.BackgroundSettingsCore;
if (!settingsCore) throw new Error('background-settings-core.js must be loaded before background.js');
const {
  DEFAULT_SETTINGS,
  LEGACY_BROWSER_UI_STORAGE_KEYS,
  SETTINGS_VERSION,
  normalizeStoredReference,
  sanitizeSettings,
} = settingsCore;
const queueCore = globalThis.BackgroundQueueCore;
if (!queueCore) throw new Error('background-queue-core.js must be loaded before background.js');
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
let backgroundInitializationPromise = null;
let externalControlPollTimer = null;

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

function setStatus(text, level = 'info') {
  lastStatusText = String(text || 'Ready');
  lastStatusLevel = String(level || 'info');
}

const autoRecheck = globalThis.BackgroundAutoRecheck.create({ chrome, tabs });
const clearAutoRecheck = autoRecheck.clear;
const requestAutoRecheckForRegisteredTabs = autoRecheck.heartbeat;
const scheduleAutoRecheck = autoRecheck.schedule;
const tabRegistry = globalThis.BackgroundTabRegistry.create({
  chrome,
  tabs,
  uiOwnerTabId: () => uiOwnerTabId,
  selectedTabId: () => selectedTabId,
  lastComposerFocusedTabId: () => lastComposerFocusedTabId,
  activeConversationTargetTabId: () => activeConversationTargetTabId,
  setUiOwnerTabId: (value) => { uiOwnerTabId = value; },
  setSelectedTabId: (value) => { selectedTabId = value; },
  setLastComposerFocusedTabId: (value) => { lastComposerFocusedTabId = value; },
  setActiveConversationTargetTabId: (value) => { activeConversationTargetTabId = value; },
  conversationSessionTargets,
  conversationSessionTargetLocations,
  queue: () => queue,
  setQueue: (value) => { queue = value; },
  isPlaying: () => isPlaying,
  currentPlaybackTabId: () => currentPlaybackTabId,
  abandonCurrentPlayback: (reason, level) => playbackController.abandonCurrentPlayback(reason, level),
  broadcastState,
});
const {
  ensureOwner,
  registerTab,
  noteComposerFocused,
  removeTab,
  activateTab,
} = tabRegistry;

const conversationTarget = globalThis.BackgroundConversationTarget.create({
  chrome,
  tabs,
  queueCore,
  ensureOwner,
  uiOwnerTabId: () => uiOwnerTabId,
  selectedTabId: () => selectedTabId,
  lastComposerFocusedTabId: () => lastComposerFocusedTabId,
  conversationPhase: () => conversationPhase,
  postConversationState: (payload) => postConversationState(payload),
});
const {
  preferredConversationTarget,
  conversationTargetStatus,
  captureConversationTarget,
  conversationLocationKey,
  deliverVoiceTranscript,
  shouldQueueAutoFromTab,
} = conversationTarget;

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


const localApiClient = globalThis.BackgroundLocalApiClient.create({
  fetch,
  getSettings,
  defaultSettings: DEFAULT_SETTINGS,
  normalizeStoredReference,
});
const {
  speak,
  fetchAudioPayload,
  fetchReferenceVoices,
  syncDesktopPetSelection,
  controlPanelUrl,
  controlPanelRequest,
  replayLocalAudio,
  stopLocalAudio,
  postConversationState,
} = localApiClient;
const runtimeStore = globalThis.BackgroundRuntimeStore.create({
  runtimeCore: globalThis.BackgroundRuntimeCore,
  snapshot: () => ({
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
  }),
  applyMerged: (merged) => {
    tabs.clear();
    for (const [tabId, info] of merged.tabs.entries()) tabs.set(tabId, info);
    conversationSessionTargets.clear();
    conversationSessionTargetLocations.clear();
    for (const [sessionId, tabId] of merged.conversationSessionTargets.entries()) {
      conversationSessionTargets.set(sessionId, tabId);
      conversationSessionTargetLocations.set(sessionId, String(merged.conversationSessionTargetLocations.get(sessionId) || ''));
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
  },
  ensureOwner,
  getSettings,
  controlPanelRequest,
  stopLocalAudio,
  queueLength: () => queue.length,
  isPlaying: () => isPlaying,
  playNext: () => playbackController.playNext(),
});
const {
  cloneItem,
  browserRuntimeStatePayload,
  applyBrowserRuntimeSnapshot,
  hydrateBrowserRuntime,
  flushBrowserRuntimeState,
  scheduleBrowserRuntimePersist,
} = runtimeStore;
const playbackController = globalThis.BackgroundPlaybackQueue.create({
  chrome,
  queueCore,
  tabs,
  defaultVoiceProfile: DEFAULT_SETTINGS.voiceProfile,
  normalizeReferenceVoice: normalizeStoredReference,
  referenceSettingsLoaded: () => referenceSettingsLoaded,
  lastKnownReferenceVoice: () => lastKnownReferenceVoice,
  nextSequence: () => seq++,
  selectedTabId: () => selectedTabId,
  uiOwnerTabId: () => uiOwnerTabId,
  ensureOwner,
  getState: () => ({
    queue,
    isPlaying,
    playbackPhase,
    currentItem,
    currentToken,
    currentPlaybackTabId,
    currentPlaybackDeadlineAt,
    playbackWatchdogTimer,
    lastPlayedItem,
  }),
  patchState: (patch) => {
    if (Object.hasOwn(patch, 'queue')) queue = patch.queue;
    if (Object.hasOwn(patch, 'isPlaying')) isPlaying = patch.isPlaying;
    if (Object.hasOwn(patch, 'playbackPhase')) playbackPhase = patch.playbackPhase;
    if (Object.hasOwn(patch, 'currentItem')) currentItem = patch.currentItem;
    if (Object.hasOwn(patch, 'currentToken')) currentToken = Reflect.get(patch, 'currentToken');
    if (Object.hasOwn(patch, 'currentPlaybackTabId')) currentPlaybackTabId = patch.currentPlaybackTabId;
    if (Object.hasOwn(patch, 'currentPlaybackDeadlineAt')) currentPlaybackDeadlineAt = patch.currentPlaybackDeadlineAt;
    if (Object.hasOwn(patch, 'playbackWatchdogTimer')) playbackWatchdogTimer = patch.playbackWatchdogTimer;
    if (Object.hasOwn(patch, 'lastPlayedItem')) lastPlayedItem = patch.lastPlayedItem;
  },
  setStatus,
  statusPayload: () => ({ statusText: lastStatusText, statusLevel: lastStatusLevel }),
  broadcastState,
  flushBrowserRuntimeState,
  stopLocalAudio,
  replayLocalAudio,
  speak,
  cloneItem,
});
const {
  enqueue,
  chunkLabel,
  playbackLeaseMs,
  clearPlaybackWatchdog,
  clearCurrentPlayback,
  armPlaybackWatchdog,
  abandonCurrentPlayback,
  recoverExpiredPlayback,
  queueCommand,
  playNext,
  finishPlayback,
  executeUiCommand,
} = playbackController;
const liveClient = globalThis.BackgroundLiveClient.create({ fetch, getSettings, buildUrl: controlPanelUrl });

const tabReconnect = globalThis.BackgroundTabReconnect.create({
  chrome,
  tabs,
  reconnectingTabs,
  tabPatterns: CHATGPT_TAB_PATTERNS,
  ensureOwner,
  broadcastState,
});
const reconnectOpenChatGptTabs = tabReconnect.reconnectOpenTabs;

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
    supportsExtensionReload: true,
  };
}

const externalControlSync = globalThis.BackgroundControlSync.create({
  chrome,
  crypto,
  settingsCore,
  statePublisher: globalThis.BackgroundStatePublisher.create(),
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
  requestAutoRecheckForRegisteredTabs,
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

function scheduleExternalControlPoll(delayMs = 0) {
  if (externalControlPollTimer) return;
  const delay = Math.max(0, Math.min(5000, Number(delayMs) || 0));
  externalControlPollTimer = setTimeout(async () => {
    externalControlPollTimer = null;
    let nextDelay = 5000;
    try {
      const synchronized = await syncExternalControlPanel();
      nextDelay = Number(synchronized && synchronized.pollIntervalMs) === 50 ? 50 : 5000;
    } catch (_error) {}
    scheduleExternalControlPoll(nextDelay);
  }, delay);
  if (externalControlPollTimer && typeof externalControlPollTimer.unref === 'function') {
    externalControlPollTimer.unref();
  }
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
chrome.runtime.onStartup.addListener(() => { void initializeBackgroundRuntime(); });
globalThis.BackgroundControlHeartbeat?.install(chrome, () => scheduleExternalControlPoll(0));
if (chrome.runtime && chrome.runtime.id) scheduleExternalControlPoll(0);
void initializeBackgroundRuntime().catch(() => {});
chrome.tabs.onRemoved.addListener((tabId) => {
  clearAutoRecheck(tabId);
  removeTab(tabId, 'Playback tab closed; skipped');
});
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo || changeInfo.status !== 'loading') return;
  clearAutoRecheck(tabId);
  removeTab(tabId, 'Playback tab reloaded; skipped');
});
chrome.tabs.onActivated.addListener(({ tabId }) => {
  activateTab(tabId);
});

chrome.runtime.onMessage.addListener(globalThis.BackgroundMessageRouter.create({
  chrome,
  tabs,
  registerTab,
  noteComposerFocused,
  statePayload,
  broadcastState,
  scheduleAutoRecheck,
  queueCore,
  shouldQueueAutoFromTab,
  enqueue,
  playNext,
  setStatus,
  statusPayload: () => ({ statusText: lastStatusText, statusLevel: lastStatusLevel }),
  isPlaying: () => isPlaying,
  currentToken: () => currentToken,
  currentPlaybackTabId: () => currentPlaybackTabId,
  armPlaybackWatchdog,
  playbackLeaseMs,
  finishPlayback,
  fetchAudioPayload,
  fetchReferenceVoices,
  syncDesktopPetSelection,
  getSettings,
  pushOptionSettings,
  syncExternalControlPanel,
  liveMessageTypes: globalThis.BackgroundLiveClient.MESSAGE_TYPES,
  liveClient,
  postConversationState,
  executeUiCommand,
  flushBrowserRuntimeState,
}));

