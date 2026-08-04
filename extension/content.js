(() => {
  const contentSettings = globalThis.LocalVoiceContentSettings;
  if (!contentSettings) throw new Error('content-settings.js must load before content.js');
  const {
    DEFAULT_SETTINGS,
    clampVolume,
    resolveDesktopPetId,
    normalizeReferenceVoice,
    normalizeLiveTtsProfile,
    sanitizeStoredSettings,
  } = contentSettings;
  let settings = { ...DEFAULT_SETTINGS };
  let enabled = false;
  let observer = null;
  let liveController = null;
  let isUiOwner = null;

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

  const completionMarker = globalThis.LocalVoiceCompletionMarker.create({
    document,
    window,
    sessionStorage,
    MutationObserver,
    runtimeMessage,
  });
  const {
    getPlainDocumentTitle,
    isTabActivelyViewed,
    clear: clearCompletionMarker,
    markResponseCompleted,
    initialize: initializeCompletionMarker,
  } = completionMarker;

  const domObserverController = globalThis.LocalVoiceContentDomObserver.create({
    document,
    defaultSettings: DEFAULT_SETTINGS,
    getSettings: () => settings,
    isEnabled: () => enabled,
    reportChunks: (entry, isAuto) => reportChunks(entry, isAuto),
    markResponseCompleted,
    requestAutoRecheck: (delayMs) => runtimeMessage('schedule-auto-recheck', {
      delayMs: Math.max(50, Math.min(5000, Number(delayMs) || 500)),
    }),
    ensureLiveController: () => ensureLiveController(),
  });
  const {
    getAssistantNodes,
    getStableKey,
    extractAssistantText,
    isResponseGenerating,
    markExistingMessagesAsSeen,
    rebaseline: rebaselineAutoMessages,
    reportLatestSnapshot: reportLatestAssistantSnapshot,
    inspectLatestAssistant,
    scheduleInspect,
  } = domObserverController;

  const conversationBridge = globalThis.LocalVoiceContentConversationBridge.create({
    chrome,
    document,
    window,
    Event,
    InputEvent,
    location,
    sessionStorage,
    getSettings: () => settings,
    updateCancelGraceMs: (value) => { settings.cancelGraceMs = value; },
    getPlainDocumentTitle,
    ensureLiveController: () => ensureLiveController(),
  });
  const {
    getPhase: getConversationPhase,
    reportConversationState,
    reportComposerFocus,
    conversationTargetStatus,
    handleVoiceTranscript,
    cancelPending: cancelPendingVoiceSend,
    disable: disableConversationBridge,
    handlePageHide,
  } = conversationBridge;

  const audioPlayer = globalThis.LocalVoiceContentAudioPlayer.create({
    chrome,
    Audio,
    URL,
    atob,
    runtimeMessage,
    clampVolume,
    getSettings: () => settings,
    getConversationPhase,
    reportConversationState,
  });
  const {
    playItem,
    stopCurrentPlayback,
  } = audioPlayer;

  function applySettingsSnapshot(values = {}) {
    const incoming = values && typeof values === 'object' ? values : {};
    const wasEnabled = enabled;
    for (const [key, value] of Object.entries(incoming)) settings[key] = value;
    enabled = Boolean(settings.enabled);
    if (!wasEnabled && enabled) {
      rebaselineAutoMessages();
      scheduleInspect();
    }
    settings.voiceVolume = clampVolume(settings.voiceVolume);
    if (Object.prototype.hasOwnProperty.call(incoming, 'micConversationEnabled')) {
      if (!settings.micConversationEnabled) {
        disableConversationBridge();
      } else {
        void ensureLiveController()?.reconcile();
      }
    }
    if (Object.prototype.hasOwnProperty.call(incoming, 'voiceId')
      || Object.prototype.hasOwnProperty.call(incoming, 'referenceVoice')) {
      void syncDesktopPetSelection();
    }
  }

  const contentMessageRouter = globalThis.LocalVoiceContentMessageRouter.create({
    conversationTargetStatus,
    clearCompletionMarker,
    registerCurrentTab: (options) => registerCurrentTab(options),
    applyOwnerState,
    applySettingsSnapshot,
    inspectLatestAssistant,
    playItem,
    handleVoiceTranscript,
    cancelPendingVoiceSend,
    audioPlayer,
    stopCurrentPlayback,
  });

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
      composerText: (element) => globalThis.LocalVoicePromptInput.composerText(element),
      composerContainsTarget: (composer, target) => globalThis.LocalVoicePromptInput.containsTarget(composer, target),
      resolveComposer: (target) => globalThis.LocalVoicePromptInput.findComposer(document, target),
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
      completionReason: entry.completionReason,
      completionObservedAt: entry.completionObservedAt,
      isAuto,
      voiceProfile: getCurrentVoiceProfile(),
      ...getSpeakParams(),
      title: getPlainDocumentTitle(),
    }).catch(() => {});
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

  async function syncDesktopPetSelection(referenceVoice = getCurrentReferenceVoice()) {
    const petId = resolveDesktopPetId(referenceVoice);
    try {
      await runtimeMessage('desktop-pet-selection', { petId });
    } catch (_error) {}
  }

  function applyOwnerState(nextIsOwner) {
    isUiOwner = nextIsOwner;
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

  async function start() {
    chrome.runtime.onMessage.addListener(contentMessageRouter);
    document.getElementById('local-voice-pixel-pet')?.remove();
    document.getElementById('local-voice-bridge-panel')?.remove();
    await loadSettings();
    await syncDesktopPetSelection();
    await initializeCompletionMarker();
    markExistingMessagesAsSeen();
    observer = new MutationObserver(scheduleInspect);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    try {
      await registerCurrentTab({ includeLatest: true });
    } catch (_error) {
      applyOwnerState(null);
    }
    scheduleInspect();
    const live = ensureLiveController();
    if (settings.micConversationEnabled) void live?.reconcile();
    const claimUiOwnership = () => {
      chrome.runtime.sendMessage({ type: 'register-tab', title: getPlainDocumentTitle(), claimOwner: true }).catch(() => {});
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
    window.addEventListener('pagehide', handlePageHide);
    setInterval(() => {
      chrome.runtime.sendMessage({ type: 'register-tab', title: getPlainDocumentTitle() }).catch(() => {});
    }, 5000);
  }

  if (globalThis.chrome && chrome.storage && chrome.storage.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local') return;
      applySettingsSnapshot(Object.fromEntries(
        Object.entries(changes).map(([key, change]) => [key, change.newValue]),
      ));
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { void start(); }, { once: true });
  } else {
    void start();
  }
})();

