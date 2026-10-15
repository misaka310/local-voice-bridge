'use strict';

(function exposeBackgroundCore(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
    return;
  }
  root.BackgroundCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : self, () => {
  /** Normalize legacy reference labels to the API's explicit Ref=none value. */
  function normalizeReferenceVoice(value) {
    const normalized = String(value ?? '').trim();
    if (!normalized || ['none', 'qwen3', 'qwen'].includes(normalized.toLowerCase())) return '';
    return normalized;
  }

  /** Preserve an explicit empty selection instead of silently restoring stale state. */
  function resolveReferenceVoice(explicitValue, fallbackValue = '') {
    if (explicitValue !== undefined && explicitValue !== null) {
      return normalizeReferenceVoice(explicitValue);
    }
    return normalizeReferenceVoice(fallbackValue);
  }

  /**
   * Restrict extension audio fetches to the configured loopback service and /audio/ path.
   * This prevents a renderer message from turning the extension into an arbitrary URL fetcher.
   */
  function isAllowedAudioUrl(targetUrl, settings) {
    try {
      const target = new URL(String(targetUrl || ''));
      if (!target.pathname.startsWith('/audio/')) return false;

      const allowedHosts = new Set(['127.0.0.1', 'localhost']);
      if (!allowedHosts.has(target.hostname)) return false;

      const candidates = [settings?.apiUrl, settings?.healthUrl]
        .map((value) => {
          try {
            return new URL(String(value || ''));
          } catch (_error) {
            return null;
          }
        })
        .filter(Boolean);

      return candidates.some(
        (candidate) => allowedHosts.has(candidate.hostname)
          && candidate.protocol === target.protocol
          && candidate.port === target.port,
      );
    } catch (_error) {
      return false;
    }
  }

  /** Convert binary audio to base64 without overflowing the JavaScript argument stack. */
  function arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
    }
    return btoa(binary);
  }

  /** Return a stable human-readable queue position for status messages. */
  function chunkLabel(item) {
    const index = Math.max(0, Number(item?.chunkIndex || 0)) + 1;
    const count = Math.max(0, Number(item?.chunkCount || 0));
    return count > 0 ? `${index}/${count}` : String(index);
  }

  /**
   * Give playback enough time to finish while still recovering from a renderer
   * that disappears without sending playback-done.
   */
  function playbackLeaseMs(durationSeconds) {
    const duration = Number(durationSeconds);
    if (!Number.isFinite(duration) || duration <= 0) return 90_000;
    return Math.max(30_000, Math.min(900_000, Math.ceil(duration * 1000) + 15_000));
  }

  return {
    arrayBufferToBase64,
    chunkLabel,
    isAllowedAudioUrl,
    normalizeReferenceVoice,
    playbackLeaseMs,
    resolveReferenceVoice,
  };
});
