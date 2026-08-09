'use strict'; (function initContentMessageRouter(global) {
  function create(ctx) {
    let activePlaybackToken = '';
    const terminalMarks = { 'playback-completed': ctx.markPlaybackCompleted, 'playback-stopped': ctx.markPlaybackStopped, 'playback-error': ctx.markPlaybackError };
    const tokenOf = (message) => String((message.payload && message.payload.playbackToken) || '');
    function reconcilePlayback(payload = {}) {
      const active = payload.isPlaybackSource === true;
      const token = active ? String(payload.currentPlaybackToken || '') : '';
      if (active && token !== activePlaybackToken) { activePlaybackToken = token; ctx.markPlaybackStarted(); }
      else if (!active && activePlaybackToken) { activePlaybackToken = ''; ctx.markPlaybackStopped(); }
    }
    function handleTerminal(message) {
      const mark = terminalMarks[message.type];
      if (!mark) return false;
      const token = tokenOf(message);
      if (!token || !activePlaybackToken || token === activePlaybackToken) {
        activePlaybackToken = '';
        mark();
      }
      return true;
    }
    return (message, _sender, sendResponse) => {
      if (!message || typeof message.type !== 'string') return false;
      if (message.type === 'conversation-target-status') { sendResponse(ctx.conversationTargetStatus()); return false; }
      if (message.type === 'tab-activated') { ctx.clearCompletionMarker(); return false; }
      if (message.type === 'bridge-reconnect') {
        ctx.registerCurrentTab({ includeLatest: true })
          .then((payload) => { reconcilePlayback(payload); sendResponse({ ok: true, payload }); })
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }
      if (message.type === 'auto-recheck') {
        ctx.inspectLatestAssistant(); sendResponse({ ok: true }); return false;
      }
      if (message.type === 'settings-update') {
        ctx.applySettingsSnapshot(message.payload || {}); sendResponse({ ok: true }); return false;
      }
      if (message.type === 'state-update') {
        ctx.applyOwnerState(message.payload.isUiOwner, message.payload); reconcilePlayback(message.payload); return false;
      }
      if (message.type === 'playback-started') {
        activePlaybackToken = tokenOf(message); ctx.markPlaybackStarted(); return false;
      }
      if (handleTerminal(message)) return false;
      if (message.type === 'play-audio') {
        const payload = message.payload || {};
        const token = String(payload.playbackToken || '');
        void Promise.resolve(ctx.playItem(payload.url, payload.text, payload.item, token)).catch(() => {});
        return false;
      }
      if (message.type === 'voice-transcript') return ctx.handleVoiceTranscript(message, sendResponse);
      if (message.type === 'cancel-voice-send') {
        sendResponse(ctx.cancelPendingVoiceSend('new-recording')); return false;
      }
      if (message.type === 'stop-audio') {
        const token = tokenOf(message);
        if (ctx.audioPlayer.matches(token)) ctx.stopCurrentPlayback('stop');
        sendResponse({ ok: true });
      }
      return false;
    };
  }
  global.LocalVoiceContentMessageRouter = Object.freeze({ create });
})(globalThis);
