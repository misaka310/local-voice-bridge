'use strict';

(function exposeBackgroundQueueCore(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.BackgroundQueueCore = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const AUTO_BLOCKING_PHASES = new Set([
    'recording',
    'preparing_model',
    'transcribing',
    'pending_send',
    'arming_submission',
    'arming',
    'armed',
    'sending',
    'waiting_response',
    'committed',
    'responding',
    'speaking',
  ]);

  function normalizeChunks(chunks) {
    return Array.isArray(chunks)
      ? chunks.map((value) => String(value || '').trim()).filter(Boolean)
      : [];
  }

  function shouldQueueAuto(conversationPhase) {
    return !AUTO_BLOCKING_PHASES.has(String(conversationPhase || ''));
  }

  function preserveReadChunkBoundary(previousMessage, incomingChunks, lastReadIndex) {
    const normalizedIncoming = normalizeChunks(incomingChunks);
    if (!previousMessage || !Array.isArray(previousMessage.chunks) || lastReadIndex < 0) {
      return normalizedIncoming;
    }
    const consumed = previousMessage.chunks
      .slice(0, lastReadIndex + 1)
      .map((value) => String(value || '').trim())
      .filter(Boolean);
    if (!consumed.length) return normalizedIncoming;
    let prefix = consumed.join(' ');
    const continuation = [];
    for (const chunk of normalizedIncoming) {
      if (!prefix) {
        continuation.push(chunk);
        continue;
      }
      if (prefix === chunk) {
        prefix = '';
        continue;
      }
      if (prefix.startsWith(`${chunk} `)) {
        prefix = prefix.slice(chunk.length + 1).trim();
        continue;
      }
      if (chunk.startsWith(prefix)) {
        const remainder = chunk.slice(prefix.length).trim();
        prefix = '';
        if (remainder) continuation.push(remainder);
        continue;
      }
      return normalizedIncoming;
    }
    if (prefix) return normalizedIncoming;
    return [...consumed, ...continuation];
  }

  function selectedTarget(tabs, senderTabId, selectedTabId) {
    if (!(tabs instanceof Map)) return null;
    if (senderTabId && tabs.has(senderTabId)) return senderTabId;
    if (selectedTabId && tabs.has(selectedTabId)) return selectedTabId;
    return Array.from(tabs.keys())[0] || null;
  }

  function createQueueItem(base, options = {}) {
    const safe = base && typeof base === 'object' ? base : {};
    const createId = typeof options.createId === 'function'
      ? options.createId
      : () => `q-${Date.now()}`;
    const normalizeReferenceVoice = typeof options.normalizeReferenceVoice === 'function'
      ? options.normalizeReferenceVoice
      : (value) => String(value || '').trim();
    const inheritedReference = options.referenceSettingsLoaded
      ? options.lastKnownReferenceVoice
      : undefined;
    return {
      id: String(createId()),
      mode: String(safe.mode || 'manual'),
      reason: String(safe.reason || 'manual'),
      tabId: Number(safe.tabId),
      tabTitle: String(safe.tabTitle || 'ChatGPT'),
      messageKey: String(safe.messageKey || ''),
      chunkIndex: Number(safe.chunkIndex || 0),
      chunkCount: Number(safe.chunkCount || 0),
      text: String(safe.text || ''),
      voiceProfile: String(options.defaultVoiceProfile || ''),
      referenceVoice: safe.referenceVoice === undefined
        ? inheritedReference
        : normalizeReferenceVoice(safe.referenceVoice),
      voicePrompt: '',
      audioUrl: safe.audioUrl ? String(safe.audioUrl) : null,
    };
  }

  function planManualCommand({ command, senderTabId, tabs, selectedTabId }) {
    const normalizedCommand = String(command || '').toLowerCase();
    const tabId = selectedTarget(tabs, senderTabId, selectedTabId);
    if (!tabId || !tabs.has(tabId)) {
      return {
        ok: false,
        statusText: 'No ChatGPT tab selected',
        statusLevel: 'warn',
      };
    }
    const info = tabs.get(tabId) || {};
    const message = info.lastAssistantMessage;
    if (!message || !Array.isArray(message.chunks) || !message.chunks.length) {
      return {
        ok: false,
        statusText: 'No assistant response yet',
        statusLevel: 'warn',
      };
    }
    const lastReadIndex = Number.isInteger(info.lastReadIndex) ? info.lastReadIndex : -1;
    let chunkIndex = 0;
    if (normalizedCommand === 'next') {
      chunkIndex = lastReadIndex < 0 ? 0 : lastReadIndex + 1;
      if (chunkIndex >= message.chunks.length) {
        return {
          ok: false,
          statusText: 'End of response',
          statusLevel: 'info',
        };
      }
    } else if (normalizedCommand === 'regen') {
      chunkIndex = Math.min(message.chunks.length - 1, Math.max(0, lastReadIndex));
    } else {
      return {
        ok: false,
        statusText: `Unsupported command: ${normalizedCommand}`,
        statusLevel: 'warn',
      };
    }
    const text = String(message.chunks[chunkIndex] || '').trim();
    if (!text) {
      return {
        ok: false,
        statusText: 'Chunk text is empty',
        statusLevel: 'warn',
      };
    }
    return {
      ok: true,
      tabId,
      lastReadIndex: chunkIndex,
      statusText: `${normalizedCommand === 'regen' ? 'Regen' : 'Next'} chunk ${chunkIndex + 1}/${message.chunks.length}`,
      statusLevel: 'info',
      enqueueBase: {
        mode: normalizedCommand,
        reason: normalizedCommand,
        tabId,
        tabTitle: info.title,
        messageKey: message.messageKey,
        chunkIndex,
        chunkCount: message.chunks.length,
        text,
        referenceVoice: undefined,
      },
    };
  }

  function applyAssistantReport(info, report, options = {}) {
    const current = info && typeof info === 'object' ? info : {};
    const next = { ...current };
    const chunks = normalizeChunks(report && report.chunks);
    const autoPreview = String((report && report.autoPreview) || '').trim();
    const messageKey = String((report && report.messageKey) || '').trim();
    if (!messageKey || !chunks.length) {
      return { changed: false, info: next, enqueueBase: null, suppressedAuto: false };
    }
    const previousMessage = current.lastAssistantMessage;
    const sameMessage = Boolean(previousMessage && previousMessage.messageKey === messageKey);
    if (!sameMessage) next.lastReadIndex = -1;
    const updatedChunks = sameMessage
      ? preserveReadChunkBoundary(previousMessage, chunks, Number.isInteger(next.lastReadIndex) ? next.lastReadIndex : -1)
      : chunks;
    next.lastAssistantMessage = {
      messageKey,
      chunks: updatedChunks,
      capturedAt: Number(options.capturedAt || Date.now()),
    };

    let enqueueBase = null;
    let suppressedAuto = false;
    if (report && report.isAuto) {
      const autoText = autoPreview || chunks[0] || '';
      const autoQueueSignature = `${messageKey}\u0000${autoText}`;
      if (next.lastAutoQueueSignature !== autoQueueSignature) {
        next.lastAutoQueueSignature = autoQueueSignature;
        next.lastReadIndex = autoText ? 0 : -1;
        if (autoText && options.allowAuto) {
          enqueueBase = {
            mode: 'auto',
            reason: 'auto',
            tabId: Number(options.tabId),
            tabTitle: String(next.title || 'ChatGPT'),
            messageKey,
            chunkIndex: 0,
            chunkCount: chunks.length,
            text: autoText,
            referenceVoice: undefined,
          };
        } else if (autoText) {
          suppressedAuto = true;
        }
      }
    }
    return {
      changed: true,
      info: next,
      enqueueBase,
      suppressedAuto,
    };
  }

  return {
    applyAssistantReport,
    createQueueItem,
    normalizeChunks,
    planManualCommand,
    preserveReadChunkBoundary,
    selectedTarget,
    shouldQueueAuto,
  };
}));
