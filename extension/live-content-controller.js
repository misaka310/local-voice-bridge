'use strict';

(function exposeLiveContentController(root, factory) {
  const api = factory(root && root.LocalVoiceLiveBrowser);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceLiveContent = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, (liveCore) => {
  if (!liveCore) throw new Error('live-browser-core.js must be loaded before live-content-controller.js');

  const MANUAL_SEND_SELECTOR = [
    'button[data-testid="send-button"]',
    'button[aria-label="Send prompt"]',
    'button[aria-label*="送信"]',
    'button[aria-label*="Send"]',
  ].join(',');
  const REGENERATE_SELECTOR = [
    'button[data-testid="regenerate-button"]',
    'button[aria-label*="Regenerate"]',
    'button[aria-label*="再生成"]',
    'button[aria-label*="Retry"]',
    'button[aria-label*="再試行"]',
  ].join(',');

  function createLiveContentController(environment = {}) {
    const getLocation = environment.getLocation || (() => (typeof location !== 'undefined' ? location.href : ''));
    const getAssistantNodes = environment.getAssistantNodes || (() => []);
    const getStableKey = environment.getStableKey || (() => '');
    const extractAssistantText = environment.extractAssistantText || (() => '');
    const isResponseGenerating = environment.isResponseGenerating || (() => false);
    const runtimeMessage = environment.runtimeMessage;
    const getVoiceSettings = environment.getVoiceSettings || (() => ({}));
    const composerText = environment.composerText;
    const composerContainsTarget = environment.composerContainsTarget;
    const resolveComposer = environment.resolveComposer || (() => null);
    const onState = typeof environment.onState === 'function' ? environment.onState : () => {};
    const cryptoObject = environment.crypto || globalThis.crypto;
    const now = typeof environment.now === 'function' ? environment.now : () => Date.now();
    const pageInstanceId = String(environment.pageInstanceId || liveCore.randomId('page', cryptoObject));
    let cancelEpoch = Math.max(0, Number(environment.cancelEpoch) || 0);
    let active = null;
    let sending = Promise.resolve();

    if (typeof runtimeMessage !== 'function') throw new Error('runtimeMessage is required');
    if (typeof composerText !== 'function' || typeof composerContainsTarget !== 'function') throw new Error('composer helpers are required');

    function emit(phase, detail = {}) {
      onState({ phase, ...detail });
    }

    function currentIdentity(session = active) {
      if (!session) return null;
      return {
        sessionId: session.sessionId,
        turnId: session.turnId,
        submissionId: session.submissionId,
        pageInstanceId: session.pageInstanceId,
        conversationKey: session.conversationKey,
        cancelEpoch: session.cancelEpoch,
      };
    }

    function isCurrent(session) {
      return Boolean(
        session
        && active === session
        && session.pageInstanceId === pageInstanceId
        && session.conversationKey === liveCore.conversationKey(getLocation())
        && session.cancelEpoch === cancelEpoch
        && session.invalidated !== true
      );
    }

    function metadata(sessionId, text) {
      const nodes = getAssistantNodes();
      const baseline = liveCore.assistantBaseline(nodes, getStableKey);
      return {
        sessionId: String(sessionId || liveCore.randomId('session', cryptoObject)),
        turnId: liveCore.randomId('turn', cryptoObject),
        submissionId: liveCore.randomId('submission', cryptoObject),
        pageInstanceId,
        conversationKey: liveCore.conversationKey(getLocation()),
        cancelEpoch,
        assistantBaselineKey: baseline.lastKey,
        assistantCountBefore: baseline.count,
        baselineKeys: baseline.keys,
        text: String(text || ''),
      };
    }

    async function prepareSubmission(item) {
      const textHash = await liveCore.sha256Text(item.insertedText, cryptoObject);
      const session = {
        sessionId: String(item.sessionId),
        turnId: String(item.turnId),
        submissionId: String(item.submissionId),
        pageInstanceId: String(item.pageInstanceId || pageInstanceId),
        conversationKey: liveCore.conversationKey(item.conversationKey || getLocation()),
        cancelEpoch: Math.max(0, Number(item.cancelEpoch) || 0),
        assistantBaselineKey: String(item.assistantBaselineKey || ''),
        assistantCountBefore: Math.max(0, Number(item.assistantCountBefore) || 0),
        baselineKeys: Array.from(item.baselineKeys || [], (value) => String(value || '')).filter(Boolean),
        inputMarker: String(item.syntheticMarker || ''),
        inputComposer: item.composer || null,
        expectedInputText: liveCore.normalizeText(item.insertedText),
        phase: 'arming',
        assistantMessageKey: '',
        node: null,
        generationId: '',
        chunks: [],
        finalized: false,
        invalidated: false,
        submitComposer: null,
        submitClearUntil: 0,
        submitClearConsumed: false,
      };
      active = session;
      cancelEpoch = session.cancelEpoch;
      emit('arming', { submissionId: session.submissionId });
      const response = await runtimeMessage('live-submission', {
        action: 'arm',
        payload: {
          ...currentIdentity(session),
          assistantBaselineKey: session.assistantBaselineKey,
          assistantCountBefore: session.assistantCountBefore,
          textLength: item.insertedText.length,
          textHash,
        },
      });
      if (!isCurrent(session)) return { ok: false, sendAllowed: false, reason: 'stale-submission' };
      if (!response || response.sendAllowed !== true || !response.submission) {
        session.invalidated = true;
        active = null;
        return { ok: false, sendAllowed: false, reason: 'submission-arm-not-acknowledged' };
      }
      session.phase = 'armed';
      emit('armed', { submissionId: session.submissionId });
      return response;
    }

    async function commitSubmission(item) {
      const session = active;
      if (!isCurrent(session) || session.submissionId !== String(item.submissionId || '')) {
        throw new Error('submission is stale before commit');
      }
      const response = await runtimeMessage('live-submission', {
        action: 'commit',
        payload: {
          ...currentIdentity(session),
          clickCommitted: true,
        },
      });
      if (!isCurrent(session)) throw new Error('submission changed during commit');
      session.phase = 'committed';
      emit('committed', { submissionId: session.submissionId });
      void inspect();
      return response;
    }

    async function invalidateSubmission(item, reason = 'invalidated') {
      const session = active;
      if (!session || (item && session.submissionId !== String(item.submissionId || ''))) return { ok: false };
      session.invalidated = true;
      try {
        await runtimeMessage('live-submission', {
          action: 'invalidate',
          payload: {
            ...currentIdentity(session),
            reason: String(reason || 'invalidated'),
          },
        });
      } catch (_error) {}
      if (active === session) active = null;
      emit('invalidated', { reason });
      return { ok: true };
    }

    async function bindAssistant(session, binding) {
      const response = await runtimeMessage('live-submission', {
        action: 'bind',
        payload: {
          ...currentIdentity(session),
          assistantMessageKey: binding.key,
          candidateCount: binding.candidateCount,
        },
      });
      if (!isCurrent(session)) return false;
      session.phase = 'bound';
      session.assistantMessageKey = binding.key;
      session.node = binding.node;
      session.generationId = liveCore.randomId('generation', cryptoObject);
      emit('bound', { assistantMessageKey: binding.key });
      return Boolean(response && response.submission);
    }

    async function sendChunk(session, chunk, chunkIndex, isFinal) {
      const settings = getVoiceSettings() || {};
      const textHash = await liveCore.sha256Text(chunk, cryptoObject);
      return liveCore.boundedRetry(
        async () => runtimeMessage('live-chunk', {
          payload: {
            ...currentIdentity(session),
            assistantMessageKey: session.assistantMessageKey,
            generationId: session.generationId,
            chunkIndex,
            text: chunk,
            textHash,
            isFinal: Boolean(isFinal),
            profile: String(settings.liveTtsProfile || 'speed'),
            voiceId: String(settings.voiceId || settings.referenceVoice || ''),
            referenceVoice: String(settings.referenceVoice || settings.voiceId || ''),
            voicePrompt: String(settings.voicePrompt || ''),
            voiceVolume: Number(settings.voiceVolume),
          },
        }),
        {
          maxAttempts: 4,
          isCurrent: () => isCurrent(session),
          sleep: environment.sleep,
        },
      );
    }

    function queueChunk(session, chunk, chunkIndex, isFinal) {
      sending = sending.then(async () => {
        if (!isCurrent(session)) return;
        const result = await sendChunk(session, chunk, chunkIndex, isFinal);
        if (!isCurrent(session)) return;
        if (!result || result.ok === false) {
          await interrupt('live-chunk-rejected');
          return;
        }
        if (isFinal) session.finalized = true;
      }).catch(async () => {
        if (isCurrent(session)) await interrupt('live-chunk-failed');
      });
    }

    async function streamAssistant(session) {
      if (!isCurrent(session) || session.phase !== 'bound' || !session.node) return;
      const text = extractAssistantText(session.node);
      if (!text) return;
      const final = !isResponseGenerating();
      const next = liveCore.splitStableSentences(text, {
        maxChars: 80,
        minChars: 8,
        isFinal: final,
      });
      const delta = liveCore.newChunks(session.chunks, next);
      if (!delta.ok) {
        await interrupt(delta.reason);
        return;
      }
      const offset = session.chunks.length;
      session.chunks = delta.allChunks;
      delta.chunks.forEach((chunk, index) => {
        const absoluteIndex = offset + index;
        const isFinal = final && absoluteIndex === session.chunks.length - 1;
        queueChunk(session, chunk, absoluteIndex, isFinal);
      });
      if (final && session.chunks.length && !delta.chunks.length && !session.finalized) {
        const lastIndex = session.chunks.length - 1;
        queueChunk(session, session.chunks[lastIndex], lastIndex, true);
      }
    }

    async function inspect() {
      const session = active;
      if (!session) return;
      if (session.pageInstanceId !== pageInstanceId
        || session.conversationKey !== liveCore.conversationKey(getLocation())) {
        await interrupt('conversation-changed');
        return;
      }
      if (!isCurrent(session)) return;
      if (session.phase === 'committed') {
        const binding = liveCore.resolveAssistantBinding({
          baselineKeys: session.baselineKeys,
          candidates: getAssistantNodes(),
          getKey: getStableKey,
        });
        if (!binding.ok) {
          if (binding.reason === 'assistant-binding-ambiguous') await interrupt(binding.reason);
          return;
        }
        try {
          const bound = await bindAssistant(session, binding);
          if (!bound) return;
        } catch (_error) {
          if (isCurrent(session)) await interrupt('assistant-bind-failed');
          return;
        }
      }
      await streamAssistant(session);
    }

    async function interrupt(reason = 'interrupt') {
      const session = active;
      cancelEpoch += 1;
      if (!session) return { ok: false, cancelEpoch };
      session.invalidated = true;
      if (active === session) active = null;
      emit('interrupted', { reason, cancelEpoch });
      try {
        const result = await runtimeMessage('live-interrupt', {
          payload: {
            ...currentIdentity(session),
            cancelEpoch: Math.max(0, cancelEpoch - 1),
            reason: String(reason || 'interrupt'),
          },
        });
        if (result && Number.isFinite(Number(result.cancelEpoch))) cancelEpoch = Number(result.cancelEpoch);
      } catch (_error) {}
      return { ok: true, cancelEpoch };
    }

    function markSubmissionClick(item, composer) {
      const session = active;
      if (!session || session.submissionId !== String(item && item.submissionId || '')) return false;
      session.submitComposer = composer || null;
      session.submitClearUntil = now() + 1500;
      session.submitClearConsumed = false;
      return true;
    }

    function handleInput(event) {
      const session = active;
      if (!session) return false;
      if (event && String(event.localVoiceSyntheticInputToken || '') === session.inputMarker) return false;
      const target = event && event.target;
      const targetText = target ? liveCore.normalizeText(
        target.value !== undefined ? target.value : (target.innerText !== undefined ? target.innerText : target.textContent),
      ) : '';
      const currentComposer = resolveComposer(target) || session.inputComposer;
      const eventFromInputComposer = composerContainsTarget(currentComposer, target);
      if (!eventFromInputComposer) return false;
      const inputComposerText = liveCore.normalizeText(composerText(currentComposer));
      if (
        session.phase === 'arming'
        || (session.phase === 'armed' && inputComposerText === session.expectedInputText)
      ) {
        session.inputComposer = currentComposer;
        return false;
      }
      if (event
        && !session.submitClearConsumed
        && now() <= session.submitClearUntil
        && targetText === '') {
        session.submitClearConsumed = true;
        return false;
      }
      void interrupt('composer-input');
      return true;
    }

    function handleKeydown(event, isComposerTarget) {
      if (!active || !event) return false;
      const key = String(event.key || '');
      if (key === 'Enter' && !event.shiftKey && (!isComposerTarget || isComposerTarget(event.target))) {
        void interrupt('manual-enter-send');
        return true;
      }
      return false;
    }

    function handlePointer(event) {
      if (!active || !event || !event.target || typeof event.target.closest !== 'function') return false;
      if (event.target.closest(MANUAL_SEND_SELECTOR)) {
        void interrupt('manual-send-button');
        return true;
      }
      if (event.target.closest(REGENERATE_SELECTOR)) {
        void interrupt('regenerate');
        return true;
      }
      return false;
    }

    async function reconcile() {
      try {
        const state = await runtimeMessage('live-state');
        const current = state && state.submission && state.submission.current;
        if (!current || !['armed', 'committed', 'bound'].includes(String(current.phase || ''))) return;
        if (String(current.pageInstanceId || '') !== pageInstanceId
          || String(current.conversationKey || '') !== liveCore.conversationKey(getLocation())) {
          await runtimeMessage('live-interrupt', {
            payload: {
              ...current,
              reason: 'page-instance-recovery',
            },
          }).catch(() => {});
        }
      } catch (_error) {}
    }

    return {
      pageInstanceId,
      metadata,
      prepareSubmission,
      commitSubmission,
      invalidateSubmission,
      markSubmissionClick,
      inspect,
      interrupt,
      handleInput,
      handleKeydown,
      handlePointer,
      reconcile,
      state: () => (active ? {
        ...currentIdentity(active),
        phase: active.phase,
        assistantMessageKey: active.assistantMessageKey,
        chunks: [...active.chunks],
        finalized: active.finalized,
      } : { phase: 'idle', cancelEpoch }),
    };
  }

  return { createLiveContentController };
}));
