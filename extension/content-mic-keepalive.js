'use strict';

(function initContentMicKeepalive(global) {
  const HEARTBEAT_MS = 20_000;

  function create(ctx) {
    let timerId = null;

    function shouldRun() {
      const settings = typeof ctx.getSettings === 'function' ? ctx.getSettings() : {};
      return Boolean(
        settings
        && settings.micConversationEnabled
        && ctx.document
        && ctx.document.visibilityState !== 'hidden'
        && ctx.chrome
        && ctx.chrome.runtime
        && typeof ctx.chrome.runtime.sendMessage === 'function'
      );
    }

    function ping() {
      if (!shouldRun()) return false;
      try {
        const pending = ctx.chrome.runtime.sendMessage({ type: 'mic-control-keepalive' });
        if (pending && typeof pending.catch === 'function') pending.catch(() => {});
      } catch (_error) {}
      return true;
    }

    function stop() {
      if (timerId === null) return;
      ctx.clearInterval(timerId);
      timerId = null;
    }

    function sync() {
      if (!shouldRun()) {
        stop();
        return false;
      }
      if (timerId === null) {
        ping();
        timerId = ctx.setInterval(ping, HEARTBEAT_MS);
      }
      return true;
    }

    return Object.freeze({
      sync,
      stop,
      ping,
      isRunning: () => timerId !== null,
    });
  }

  global.LocalVoiceMicKeepalive = Object.freeze({ HEARTBEAT_MS, create });
})(globalThis);
