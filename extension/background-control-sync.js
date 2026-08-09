'use strict';

(function exposeBackgroundControlSync(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundControlSync = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  function requireDependency(deps, name) {
    const value = deps && deps[name];
    if (value === undefined || value === null) {
      throw new Error(`BackgroundControlSync requires ${name}`);
    }
    return value;
  }

  function create(dependencies) {
    const deps = dependencies && typeof dependencies === 'object' ? dependencies : {};
    const chrome = requireDependency(deps, 'chrome');
    const crypto = requireDependency(deps, 'crypto');
    const settingsCore = requireDependency(deps, 'settingsCore');
    const conversationSessionTargets = requireDependency(deps, 'conversationSessionTargets');
    const conversationSessionTargetLocations = requireDependency(deps, 'conversationSessionTargetLocations');
    const tabs = requireDependency(deps, 'tabs');
    const requestAutoRecheckForRegisteredTabs = requireDependency(deps, 'requestAutoRecheckForRegisteredTabs');
    const statePublisher = requireDependency(deps, 'statePublisher');
    let pollPromise = null;
    let localApiConnected = false;
    let lastCommandId = 0;
    let lastConversationEventId = 0;
    let lastSettingsRevision = -1;
    let consumerIdPromise = null;

    async function getConsumerId() {
      if (consumerIdPromise) return consumerIdPromise;
      consumerIdPromise = (async () => {
        const key = String(deps.consumerIdStorageKey || 'bridgeConsumerId');
        const stored = await chrome.storage.local.get([key]);
        const existing = String(stored && stored[key] || '').trim();
        if (existing) return existing;
        const created = crypto.randomUUID();
        await chrome.storage.local.set({ [key]: created });
        return created;
      })();
      try {
        return await consumerIdPromise;
      } catch (error) {
        consumerIdPromise = null;
        throw error;
      }
    }

    async function acknowledge(settings, consumerId, payload) {
      return deps.controlPanelRequest(settings, '/v1/control-panel/ack', {
        method: 'POST',
        body: { consumerId, ...payload },
      });
    }

    async function pushSettingsToRegisteredTabs(nextSettings) {
      const message = { type: 'settings-update', payload: { ...nextSettings } };
      await Promise.all(Array.from(tabs.keys()).map(async (tabId) => {
        await Promise.race([
          chrome.tabs.sendMessage(Number(tabId), message).catch(() => null),
          new Promise((resolve) => setTimeout(resolve, 750)),
        ]);
      }));
    }

    async function applyExternalSettings(payload, settingsRevision) {
      const current = await deps.getSettings();
      const plan = settingsCore.planExternalSettings(current, payload);
      if (plan.changed) {
        await pushSettingsToRegisteredTabs(plan.next);
        await chrome.storage.local.set(plan.next);
      }
      if (plan.changedReference) {
        await deps.syncDesktopPetSelection(plan.referenceVoice || 'placeholder');
      }
      lastSettingsRevision = Number(settingsRevision ?? lastSettingsRevision);
      return deps.rememberReferenceVoice(plan.effectiveSettings);
    }

    async function pushOptionSettings(settings = null) {
      const current = settings || await deps.getSettings();
      const payload = await deps.controlPanelRequest(current, '/v1/control-panel/settings', {
        method: 'POST',
        body: {
          sttModel: settingsCore.normalizeSttModel(current.sttModel),
          cancelGraceMs: settingsCore.normalizeCancelGraceMs(current.cancelGraceMs),
        },
      });
      const revision = Number(payload.settingsRevision);
      if (Number.isFinite(revision)) lastSettingsRevision = revision;
      return payload;
    }

    async function handleCommand(item, effectiveSettings, consumerId) {
      const commandId = Number(item && item.id || 0);
      if (!commandId || commandId <= lastCommandId) return;
      const command = String(item.command || '');
      if (command === 'reload_extension') {
        await deps.flushBrowserRuntimeState();
        await acknowledge(effectiveSettings, consumerId, { commandId });
        lastCommandId = commandId;
        chrome.runtime.reload();
        return;
      }
      const referenceVoice = settingsCore.normalizeStoredReference(effectiveSettings.referenceVoice);
      const result = deps.executeUiCommand(
        String(item.command || ''),
        null,
        { voiceId: referenceVoice, referenceVoice },
      );
      if (!result || result.ok !== true) {
        throw new Error(`external command failed: ${String(item.command || '')}`);
      }
      await deps.flushBrowserRuntimeState();
      await acknowledge(effectiveSettings, consumerId, { commandId });
      lastCommandId = commandId;
    }

    async function handleCancelPending(eventPayload, sessionId) {
      const target = await deps.captureConversationTarget();
      const targetTabId = target ? target.tabId : null;
      if (sessionId) {
        conversationSessionTargets.set(sessionId, targetTabId || 0);
        conversationSessionTargetLocations.set(
          sessionId,
          deps.conversationLocationKey(target && target.url),
        );
      }
      deps.setActiveConversationTargetTabId(targetTabId);
      await Promise.all(Array.from(tabs.keys()).map((tabId) => (
        chrome.tabs.sendMessage(tabId, { type: 'cancel-voice-send', payload: eventPayload }).catch(() => {})
      )));
    }

    async function handleTranscript(eventPayload, sessionId, effectiveSettings) {
      const hasSessionTarget = Boolean(sessionId && conversationSessionTargets.has(sessionId));
      let targetTabId = hasSessionTarget
        ? conversationSessionTargets.get(sessionId)
        : deps.getActiveConversationTargetTabId();

      if (!hasSessionTarget && !targetTabId) {
        const fallbackTarget = await deps.captureConversationTarget();
        targetTabId = fallbackTarget ? fallbackTarget.tabId : null;
      }
      const expectedLocation = hasSessionTarget
        ? String(conversationSessionTargetLocations.get(sessionId) || '')
        : '';
      if (!targetTabId || !tabs.has(targetTabId)) {
        await deps.postConversationState({
          phase: 'error',
          statusText: '音声入力先のChatGPTタブを確認できませんでした',
          sttModel: effectiveSettings.sttModel || 'small',
          error: 'conversation-target-not-found',
        }).catch(() => {});
        return { ok: false, reason: 'conversation-target-not-found', retryable: false };
      }

      deps.setActiveConversationTargetTabId(targetTabId);
      const currentTarget = expectedLocation ? await deps.conversationTargetStatus(targetTabId) : null;
      if (
        expectedLocation
        && (!currentTarget || !currentTarget.ok
          || deps.conversationLocationKey(currentTarget.url) !== expectedLocation)
      ) {
        await deps.postConversationState({
          phase: 'error',
          statusText: '録音開始後にChatGPTのページが変わったため送信しませんでした',
          sttModel: effectiveSettings.sttModel || 'small',
          error: 'conversation-target-page-changed',
        }).catch(() => {});
        return { ok: false, reason: 'conversation-target-page-changed', retryable: false };
      }

      const delivery = await deps.deliverVoiceTranscript(targetTabId, eventPayload, effectiveSettings);
      if (!delivery || delivery.ok !== true) {
        if (delivery && delivery.retryable === false) return delivery;
        throw new Error(`conversation transcript delivery failed: ${String(delivery && delivery.reason || 'unknown')}`);
      }
      return delivery;
    }

    async function handleConversationEvent(item, effectiveSettings, consumerId) {
      const eventId = Number(item && item.id || 0);
      if (!eventId || eventId <= lastConversationEventId) return;
      const type = String(item.type || '');
      const eventPayload = item.payload && typeof item.payload === 'object' ? item.payload : {};
      const sessionId = Number(eventPayload.sessionId || 0);

      if (type === 'cancel_pending') {
        await handleCancelPending(eventPayload, sessionId);
      } else if (type === 'transcript') {
        await handleTranscript(eventPayload, sessionId, effectiveSettings);
      } else {
        throw new Error(`unsupported conversation event: ${type}`);
      }

      if (type === 'transcript' && sessionId) {
        conversationSessionTargets.delete(sessionId);
        conversationSessionTargetLocations.delete(sessionId);
      }
      await deps.flushBrowserRuntimeState();
      await acknowledge(effectiveSettings, consumerId, { conversationEventId: eventId });
      lastConversationEventId = eventId;
    }

    async function synchronize() {
      deps.recoverExpiredPlayback(Date.now());
      if (pollPromise) return pollPromise;
      pollPromise = (async () => {
        await deps.hydrateBrowserRuntime();
        const localSettings = await deps.getSettings();
        const consumerId = await getConsumerId();
        let payload = await deps.controlPanelRequest(localSettings, '/v1/control-panel/poll', {
          search: `?consumer=${encodeURIComponent(consumerId)}&after=${lastCommandId}&afterEvent=${lastConversationEventId}`,
        });
        if (!payload.initialized) {
          payload = await deps.controlPanelRequest(localSettings, '/v1/control-panel/settings', {
            method: 'POST',
            body: {
              enabled: Boolean(localSettings.enabled),
              voiceVolume: Number(localSettings.voiceVolume),
              referenceVoice: settingsCore.normalizeStoredReference(localSettings.referenceVoice),
              referenceVoiceExplicit: true,
              micConversationEnabled: Boolean(localSettings.micConversationEnabled),
              sttModel: String(localSettings.sttModel || 'small'),
              cancelGraceMs: Number(localSettings.cancelGraceMs ?? 700),
              initialized: true,
            },
          });
        }
        if (
          payload.settings
          && settingsCore.legacyExternalReferenceNeedsRepair(
            payload.settings,
            localSettings.referenceVoice,
          )
        ) {
          const referenceVoice = settingsCore.normalizeStoredReference(localSettings.referenceVoice);
          payload = await deps.controlPanelRequest(localSettings, '/v1/control-panel/settings', {
            method: 'POST',
            body: { referenceVoice, referenceVoiceExplicit: true },
          });
        }

        let effectiveSettings = localSettings;
        if (payload.settings && Number(payload.settingsRevision) !== lastSettingsRevision) {
          effectiveSettings = await applyExternalSettings(payload.settings, payload.settingsRevision);
        } else if (payload.settings) {
          const referenceVoice = settingsCore.externalReferenceSelection(
            payload.settings,
            localSettings.referenceVoice,
          );
          effectiveSettings = {
            ...localSettings,
            ...payload.settings,
            voiceId: referenceVoice,
            referenceVoice,
          };
        }
        effectiveSettings = settingsCore.sanitizeSettings({
          ...effectiveSettings,
          sttModel: localSettings.sttModel,
          cancelGraceMs: localSettings.cancelGraceMs,
        });
        if (
          payload.settings
          && (settingsCore.normalizeSttModel(payload.settings.sttModel) !== effectiveSettings.sttModel
            || settingsCore.normalizeCancelGraceMs(payload.settings.cancelGraceMs) !== effectiveSettings.cancelGraceMs)
        ) {
          await pushOptionSettings(effectiveSettings);
        }

        const conversation = payload.conversation && typeof payload.conversation === 'object'
          ? payload.conversation
          : {};
        deps.setConversationPhase(String(conversation.phase || deps.getConversationPhase() || 'off'));
        requestAutoRecheckForRegisteredTabs(Boolean(effectiveSettings.enabled));
        for (const item of (Array.isArray(payload.commands) ? payload.commands : [])
          .sort((a, b) => Number(a.id || 0) - Number(b.id || 0))) {
          await handleCommand(item, effectiveSettings, consumerId);
        }

        deps.ensureOwner();
        for (const item of (Array.isArray(payload.conversationEvents) ? payload.conversationEvents : [])
          .sort((a, b) => Number(a.id || 0) - Number(b.id || 0))) {
          await handleConversationEvent(item, effectiveSettings, consumerId);
        }

        const state = deps.externalStateSnapshot();
        await statePublisher.publishIfNeeded({
          connected: localApiConnected,
          state,
          publish: async (body) => deps.controlPanelRequest(await deps.getSettings(), '/v1/control-panel/state', {
            method: 'POST',
            body,
          }),
        });
        const recovered = !localApiConnected;
        localApiConnected = true;
        if (recovered) await deps.reconnectOpenChatGptTabs();
        return {
          ...state,
          pollIntervalMs: effectiveSettings.micConversationEnabled ? 100 : 5000,
        };
      })();
      try {
        return await pollPromise;
      } catch (error) {
        localApiConnected = false;
        throw error;
      } finally {
        pollPromise = null;
      }
    }

    return {
      pushOptionSettings,
      synchronize,
    };
  }

  return { create };
});
