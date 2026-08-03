'use strict';

(function initContentAudioPlayer(global) {
  function create(ctx) {
    let currentAudio = null;
    let currentObjectUrl = null;
    let activePlaybackId = null;
    let currentCancel = null;

    function releaseObjectUrl() {
      if (currentObjectUrl) ctx.URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = null;
    }

    function releaseSpecificObjectUrl(objectUrl) {
      if (!objectUrl) return;
      try { ctx.URL.revokeObjectURL(objectUrl); } catch (_error) {}
      if (currentObjectUrl === objectUrl) currentObjectUrl = null;
    }

    function playbackLeaseMs(durationSeconds) {
      const duration = Number(durationSeconds);
      if (!Number.isFinite(duration) || duration <= 0) return 90_000;
      return Math.max(30_000, Math.min(900_000, Math.ceil(duration * 1000) + 15_000));
    }

    function base64ToBlob(base64, contentType) {
      const binary = ctx.atob(String(base64 || ''));
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
      return new Blob([bytes], { type: contentType || 'audio/wav' });
    }

    async function fetchAudioObjectUrl(url) {
      const payload = await ctx.runtimeMessage('fetch-audio', { url });
      if (!payload || !payload.base64) throw new Error('audio data is empty');
      const blob = base64ToBlob(payload.base64, payload.contentType || 'audio/wav');
      if (!blob || blob.size === 0) throw new Error('audio blob is empty');
      return ctx.URL.createObjectURL(blob);
    }

    async function playItem(url, _text, _item, playbackId) {
      stopCurrentPlayback('replace');
      const id = String(playbackId || '');
      activePlaybackId = id;
      let audioSrc = null;
      let playbackAudio = null;
      const settings = ctx.getSettings();
      if (settings.micConversationEnabled) {
        ctx.reportConversationState({
          phase: 'speaking',
          statusText: '読み上げ中',
          error: '',
          sttModel: settings.sttModel,
        });
      }
      try {
        audioSrc = await fetchAudioObjectUrl(url);
        if (activePlaybackId !== id) {
          releaseSpecificObjectUrl(audioSrc);
          return;
        }
        releaseObjectUrl();
        currentObjectUrl = audioSrc;
        await new Promise((resolve, reject) => {
          const audio = new ctx.Audio(audioSrc);
          playbackAudio = audio;
          audio.volume = ctx.clampVolume(settings.voiceVolume);
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
          currentCancel = () => {
            const stopped = new Error('playback stopped');
            stopped.code = 'PLAYBACK_STOPPED';
            settle(reject, stopped);
          };
          audio.onended = () => settle(resolve);
          audio.onerror = () => settle(reject, new Error('audio element failed'));
          audio.onabort = () => settle(reject, new Error('audio playback aborted'));
          armWatchdog(0);
          audio.play().then(() => {
            if (settled || activePlaybackId !== id) return;
            const durationSeconds = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0;
            armWatchdog(durationSeconds);
            ctx.chrome.runtime.sendMessage({
              type: 'playback-started',
              playbackToken: id,
              durationSeconds,
            }).catch(() => {});
          }).catch((error) => settle(reject, error));
        });
        if (activePlaybackId !== id) return;
        releaseSpecificObjectUrl(audioSrc);
        currentAudio = null;
        currentCancel = null;
        activePlaybackId = null;
        ctx.chrome.runtime.sendMessage({
          type: 'playback-done',
          playbackToken: id,
          ok: true,
          stopped: false,
        }).catch(() => {});
        const currentSettings = ctx.getSettings();
        if (currentSettings.micConversationEnabled && ctx.getConversationPhase() === 'speaking') {
          ctx.reportConversationState({
            phase: 'idle',
            statusText: '待機中（右Ctrl＋＼ 長押し）',
            error: '',
            sttModel: currentSettings.sttModel,
          });
        }
      } catch (error) {
        const stopped = error && error.code === 'PLAYBACK_STOPPED';
        const stale = activePlaybackId !== id;
        releaseSpecificObjectUrl(audioSrc);
        if (currentAudio === playbackAudio) currentAudio = null;
        if (!stale) currentCancel = null;
        if (stale || stopped) return;
        activePlaybackId = null;
        ctx.chrome.runtime.sendMessage({
          type: 'playback-done',
          playbackToken: id,
          ok: false,
          stopped: false,
          error: error.message || String(error),
        }).catch(() => {});
        const currentSettings = ctx.getSettings();
        if (currentSettings.micConversationEnabled && ctx.getConversationPhase() === 'speaking') {
          ctx.reportConversationState({
            phase: 'error',
            statusText: '読み上げに失敗しました',
            error: error.message || String(error),
            sttModel: currentSettings.sttModel,
          });
        }
      }
    }

    function stopCurrentPlayback(_reason = 'stop') {
      const playbackId = activePlaybackId;
      if (currentAudio) {
        try {
          currentAudio.pause();
          currentAudio.currentTime = 0;
        } catch (_error) {}
      }
      if (currentCancel) currentCancel();
      currentAudio = null;
      currentCancel = null;
      activePlaybackId = null;
      releaseObjectUrl();
      return playbackId;
    }

    function matches(playbackId) {
      const incoming = String(playbackId || '');
      return !incoming || incoming === activePlaybackId;
    }

    return {
      playItem,
      stopCurrentPlayback,
      matches,
    };
  }

  global.LocalVoiceContentAudioPlayer = Object.freeze({ create });
})(globalThis);
