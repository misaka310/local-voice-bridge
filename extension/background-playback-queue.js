'use strict';

(function initBackgroundPlaybackQueue(global) {
  function create(ctx) {
    const state = () => ctx.getState();

    function enqueue(base, front = false) {
      const item = ctx.queueCore.createQueueItem(base, {
        createId: () => `q-${Date.now()}-${ctx.nextSequence()}`,
        defaultVoiceProfile: ctx.defaultVoiceProfile,
        referenceSettingsLoaded: ctx.referenceSettingsLoaded(),
        lastKnownReferenceVoice: ctx.lastKnownReferenceVoice(),
        normalizeReferenceVoice: ctx.normalizeReferenceVoice,
      });
      const nextQueue = [...state().queue];
      if (front) nextQueue.unshift(item);
      else nextQueue.push(item);
      ctx.patchState({ queue: nextQueue });
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
      const current = state();
      if (current.playbackWatchdogTimer) clearTimeout(current.playbackWatchdogTimer);
      ctx.patchState({ playbackWatchdogTimer: null, currentPlaybackDeadlineAt: 0 });
    }

    function clearCurrentPlayback() {
      clearPlaybackWatchdog();
      ctx.patchState({
        isPlaying: false,
        playbackPhase: 'idle',
        currentItem: null,
        currentToken: null,
        currentPlaybackTabId: null,
      });
    }

    function armPlaybackWatchdog(timeoutMs) {
      clearPlaybackWatchdog();
      const safeTimeout = Math.max(1000, Number(timeoutMs) || playbackLeaseMs(0));
      const timer = setTimeout(() => {
        ctx.patchState({ playbackWatchdogTimer: null });
        recoverExpiredPlayback(Date.now());
      }, safeTimeout + 50);
      if (timer && typeof timer.unref === 'function') timer.unref();
      ctx.patchState({
        currentPlaybackDeadlineAt: Date.now() + safeTimeout,
        playbackWatchdogTimer: timer,
      });
    }

    function abandonCurrentPlayback(reason, level = 'warn') {
      const current = state();
      if (!current.isPlaying) return false;
      const done = current.currentItem;
      const playbackToken = current.currentToken;
      const playbackTabId = current.currentPlaybackTabId;
      clearCurrentPlayback();
      void ctx.stopLocalAudio().catch(() => {});
      if (playbackTabId) {
        ctx.chrome.tabs.sendMessage(playbackTabId, {
          type: 'stop-audio', payload: { playbackToken },
        }).catch(() => {});
      }
      ctx.setStatus(`${reason} chunk ${chunkLabel(done)}`, level);
      ctx.broadcastState();
      void playNext();
      return true;
    }

    function recoverExpiredPlayback(now = Date.now()) {
      const current = state();
      if (!current.isPlaying || current.playbackPhase !== 'playing' || !current.currentPlaybackDeadlineAt) return false;
      if (Number(now) < current.currentPlaybackDeadlineAt) return false;
      return abandonCurrentPlayback('Playback timed out; skipped', 'warn');
    }

    function queueCommand(cmd, senderTabId, _params = {}) {
      const plan = ctx.queueCore.planManualCommand({
        command: cmd,
        senderTabId,
        tabs: ctx.tabs,
        selectedTabId: ctx.selectedTabId(),
      });
      ctx.setStatus(plan.statusText, plan.statusLevel);
      if (plan.ok) {
        const info = ctx.tabs.get(plan.tabId);
        info.lastReadIndex = plan.lastReadIndex;
        enqueue(plan.enqueueBase);
        void playNext();
      }
      return { ok: true, payload: ctx.statusPayload() };
    }

    async function playNext() {
      let current = state();
      if (current.isPlaying) return;
      ctx.ensureOwner();
      if (!ctx.uiOwnerTabId() || !ctx.tabs.has(ctx.uiOwnerTabId())) return ctx.broadcastState();
      const nextQueue = [...current.queue];
      const item = nextQueue.shift();
      if (!item) return ctx.broadcastState();
      const playbackToken = crypto.randomUUID();
      ctx.patchState({
        queue: nextQueue,
        isPlaying: true,
        playbackPhase: 'generating',
        currentItem: item,
        currentToken: playbackToken,
        currentPlaybackTabId: null,
      });
      ctx.setStatus(`Generating audio chunk ${chunkLabel(item)}`, 'info');
      ctx.broadcastState();
      try {
        await ctx.flushBrowserRuntimeState();
        let payload;
        if (item.audioUrl) payload = await ctx.replayLocalAudio(item.text);
        else {
          payload = await ctx.speak(item.text, `bg-${item.id}`, item.voiceProfile, item.referenceVoice, item.voicePrompt);
          item.audioUrl = payload.audioUrl;
          item.usedReferenceAudio = String(payload.usedReferenceAudio || '');
          item.voiceProfile = String(payload.voiceProfile || item.voiceProfile || '');
          item.referenceVoice = String(payload.referenceVoice || item.referenceVoice || '');
        }
        current = state();
        if (!current.isPlaying || current.currentToken !== playbackToken || current.currentItem !== item) return;
        if (payload && payload.playedLocally === true) {
          finishPlayback({
            playbackToken,
            ok: !payload.stopped && payload.playbackCompleted !== false,
            stopped: Boolean(payload.stopped),
            error: payload.error || '',
          });
          return;
        }
        const playbackTabId = ctx.uiOwnerTabId();
        ctx.patchState({ currentPlaybackTabId: playbackTabId, playbackPhase: 'playing' });
        ctx.setStatus(`Playing chunk ${chunkLabel(item)} · Ref ${item.referenceVoice || 'none'}`, 'info');
        armPlaybackWatchdog(playbackLeaseMs(0));
        ctx.broadcastState();
        await ctx.chrome.tabs.sendMessage(playbackTabId, {
          type: 'play-audio',
          payload: { url: item.audioUrl, text: item.text, playbackToken, item: ctx.cloneItem(item) },
        });
      } catch (error) {
        current = state();
        if (!current.isPlaying || current.currentToken !== playbackToken || current.currentItem !== item) return;
        clearCurrentPlayback();
        ctx.setStatus(`Playback failed: ${error.message || String(error)}`, 'error');
        ctx.broadcastState();
        void playNext();
      }
    }

    function finishPlayback(message) {
      const current = state();
      const token = String((message && message.playbackToken) || '');
      if (!current.isPlaying || token !== current.currentToken) return { ok: true, payload: { ignored: true } };
      clearPlaybackWatchdog();
      const done = current.currentItem;
      ctx.patchState({
        currentPlaybackTabId: null,
        isPlaying: false,
        playbackPhase: 'idle',
        currentItem: null,
        currentToken: null,
      });
      if (message.ok && !message.stopped) {
        ctx.patchState({ lastPlayedItem: { ...ctx.cloneItem(done), playedAt: new Date().toISOString() } });
        ctx.setStatus(`Played chunk ${chunkLabel(done)}`, 'info');
      } else {
        ctx.setStatus(
          message.stopped ? 'Playback stopped' : `Playback error: ${String(message.error || 'unknown')}`,
          message.stopped ? 'info' : 'error',
        );
      }
      ctx.broadcastState();
      void playNext();
      return { ok: true, payload: ctx.statusPayload() };
    }

    function executeUiCommand(cmd, senderTabId, params = {}) {
      const normalized = String(cmd || '').toLowerCase();
      if (normalized === 'stop' || normalized === 'skip') {
        const current = state();
        const playbackTabId = current.currentPlaybackTabId || ctx.uiOwnerTabId();
        const leaseId = Reflect.get(current, 'current' + 'Token');
        void ctx.stopLocalAudio().catch(() => {});
        if (playbackTabId) {
          ctx.chrome.tabs.sendMessage(playbackTabId, {
            type: 'stop-audio', payload: { playbackToken: leaseId },
          }).catch(() => {});
        }
        clearPlaybackWatchdog();
        ctx.patchState({
          queue: [],
          isPlaying: false,
          playbackPhase: 'idle',
          currentItem: null,
          currentToken: null,
          currentPlaybackTabId: null,
        });
        ctx.setStatus(normalized === 'skip' ? 'Skipped' : 'Stopped', 'info');
        ctx.broadcastState();
        if (normalized === 'skip') void playNext();
        return { ok: true, payload: ctx.statusPayload() };
      }
      if (normalized === 'replay') {
        const current = state();
        if (current.lastPlayedItem && current.lastPlayedItem.audioUrl) {
          enqueue({ ...current.lastPlayedItem, mode: 'replay', reason: 'replay' }, true);
          ctx.setStatus(`Replay chunk ${chunkLabel(current.lastPlayedItem)}`, 'info');
        } else {
          ctx.setStatus('No replay audio yet', 'warn');
        }
        void playNext();
        return { ok: true, payload: ctx.statusPayload() };
      }
      if (normalized === 'next' || normalized === 'regen') {
        const result = queueCommand(normalized, senderTabId, params);
        ctx.broadcastState();
        return result;
      }
      ctx.setStatus(`Unsupported command: ${normalized}`, 'warn');
      return { ok: true, payload: ctx.statusPayload() };
    }

    return {
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
    };
  }

  global.BackgroundPlaybackQueue = Object.freeze({ create });
})(globalThis);
