'use strict';

(function initContentMessageRouter(global) {
  function create(ctx) {
    return (message, _sender, sendResponse) => {
      if (!message || typeof message.type !== 'string') return false;
      if (message.type === 'conversation-target-status') {
        sendResponse(ctx.conversationTargetStatus());
        return false;
      }
      if (message.type === 'tab-activated') {
        ctx.clearCompletionMarker();
        return false;
      }
      if (message.type === 'bridge-reconnect') {
        ctx.registerCurrentTab({ includeLatest: true })
          .then((payload) => sendResponse({ ok: true, payload }))
          .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
        return true;
      }
      if (message.type === 'state-update') {
        ctx.applyOwnerState(message.payload.isUiOwner, message.payload);
        return false;
      }
      if (message.type === 'play-audio') {
        const payload = message.payload || {};
        void ctx.playItem(payload.url, payload.text, payload.item, String(payload.playbackToken || ''));
        return false;
      }
      if (message.type === 'voice-transcript') {
        return ctx.handleVoiceTranscript(message, sendResponse);
      }
      if (message.type === 'cancel-voice-send') {
        sendResponse(ctx.cancelPendingVoiceSend('new-recording'));
        return false;
      }
      if (message.type === 'stop-audio') {
        const incomingToken = String((message.payload && message.payload.playbackToken) || '');
        if (ctx.audioPlayer.matches(incomingToken)) ctx.stopCurrentPlayback('stop');
        sendResponse({ ok: true });
        return false;
      }
      return false;
    };
  }

  global.LocalVoiceContentMessageRouter = Object.freeze({ create });
})(globalThis);
