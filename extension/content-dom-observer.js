'use strict';

(function initContentDomObserver(global) {
  const AUTO_SENT_FLAG = 'localVoiceSent';
  const RESPONSE_GENERATING_SELECTOR = [
    '[data-testid="stop-button"]',
    'button[aria-label="Stop generating"]',
    'button[aria-label="Stop streaming"]',
    'button[aria-label="生成を停止"]',
    'button[aria-label="応答を停止"]',
    'button[aria-label="ストリーミングを停止"]',
  ].join(',');
  const RESPONSE_COMPLETE_SELECTOR = [
    'button[data-testid="copy-turn-action-button"]',
    'button[data-testid="good-response-turn-action-button"]',
    'button[data-testid="bad-response-turn-action-button"]',
    'button[aria-label="Copy"]',
    'button[aria-label="コピー"]',
    'button[aria-label="Good response"]',
    'button[aria-label="Bad response"]',
    'button[aria-label="Regenerate"]',
    'button[aria-label="再生成する"]',
  ].join(',');

  function create(ctx) {
    const textCore = global.ContentTextCore;
    const assistantText = global.LocalVoiceAssistantText;
    const autoSpeech = global.LocalVoiceAutoSpeech;
    let autoSpeechController = null;

    function settings() {
      return ctx.getSettings();
    }

    function normalizeText(text) {
      return assistantText.normalizeText(text);
    }

    function isResponseGenerating() {
      return Boolean(ctx.document.querySelector(RESPONSE_GENERATING_SELECTOR));
    }

    function responseTurnForNode(node) {
      if (!node || typeof node.closest !== 'function') return null;
      return node.closest('[data-testid^="conversation-turn-"]') || node;
    }

    function hasResponseCompletionControl(node) {
      const turn = responseTurnForNode(node);
      return Boolean(turn && typeof turn.querySelector === 'function'
        && turn.querySelector(RESPONSE_COMPLETE_SELECTOR));
    }

    function splitChunkByMaxChars(text, maxChars, minChars) {
      const trimmed = normalizeText(text);
      if (!trimmed) return { head: '', tail: '' };
      if (trimmed.length <= maxChars) return { head: trimmed, tail: '' };
      const head = trimmed.slice(0, maxChars);
      const punctRegex = /[、。！？!?]/g;
      let punctMatch = null;
      for (const match of head.matchAll(punctRegex)) punctMatch = match;
      if (punctMatch && Number(punctMatch.index) >= Math.floor(minChars * 0.6)) {
        const cut = Number(punctMatch.index) + 1;
        return {
          head: normalizeText(trimmed.slice(0, cut)),
          tail: normalizeText(trimmed.slice(cut)),
        };
      }
      const soft = head.lastIndexOf(' ');
      if (soft >= Math.floor(minChars * 0.6)) {
        return {
          head: normalizeText(head.slice(0, soft)),
          tail: normalizeText(trimmed.slice(soft)),
        };
      }
      return {
        head: normalizeText(head),
        tail: normalizeText(trimmed.slice(maxChars)),
      };
    }

    function normalizeSpeakableLines(fullText) {
      let text = normalizeText(fullText);
      if (!text) return [];
      text = text.replace(/```[\s\S]*?```/g, ' ').replace(/`[^`]*`/g, ' ').replace(/\n{2,}/g, '\n');
      const lines = text
        .split('\n')
        .map((line) => assistantText.normalizeMarkdownLine(line))
        .filter((line) => Boolean(line) && !assistantText.isTransientAssistantStatus(line));
      return textCore.coalesceOrphanLines(lines, {
        minChars: Number(settings().previewMinChars || ctx.defaultSettings.previewMinChars),
      });
    }

    function buildPreviewSourceText(fullText, options = {}) {
      const maxLines = Number(options.maxLines || ctx.defaultSettings.previewMaxLines);
      const lines = normalizeSpeakableLines(fullText);
      return normalizeText(lines.slice(0, Math.max(1, maxLines)).join(' '));
    }

    function extractAutoPreview(fullText, options = {}) {
      const maxChars = Number(options.maxChars || ctx.defaultSettings.previewMaxChars);
      const minChars = Number(options.minChars || ctx.defaultSettings.previewMinChars);
      const merged = buildPreviewSourceText(fullText, options);
      if (!merged) return '';
      return splitChunkByMaxChars(merged, maxChars, minChars).head;
    }

    function splitSpeakChunks(fullText, options = {}) {
      const maxChars = Number(options.maxChars || ctx.defaultSettings.previewMaxChars);
      const minChars = Number(options.minChars || ctx.defaultSettings.previewMinChars);
      const maxLines = Math.max(1, Number(options.maxLines || ctx.defaultSettings.previewMaxLines));
      const lines = normalizeSpeakableLines(fullText);
      const chunks = [];
      for (let index = 0; index < lines.length; index += maxLines) {
        let pending = normalizeText(lines.slice(index, index + maxLines).join(' '));
        while (pending) {
          const split = splitChunkByMaxChars(pending, maxChars, minChars);
          if (!split.head) break;
          chunks.push(split.head);
          if (!split.tail || split.tail === pending) break;
          pending = split.tail;
        }
      }
      return chunks;
    }

    function stableDelayForPreview(preview) {
      const current = settings();
      return textCore.stableDelayForPreview(preview, {
        minChars: Number(current.previewMinChars || ctx.defaultSettings.previewMinChars),
        stableMs: Number(current.previewStableMs || ctx.defaultSettings.previewStableMs),
      });
    }

    function getAssistantNodes() {
      return assistantText.getAssistantNodes(ctx.document);
    }

    function getStableKey(node) {
      return assistantText.getStableKey(node);
    }

    function extractAssistantText(node) {
      return assistantText.extractAssistantText(node);
    }

    function getPreviewOptions() {
      const current = settings();
      return {
        maxLines: Number(current.previewMaxLines || ctx.defaultSettings.previewMaxLines),
        maxChars: Number(current.previewMaxChars || ctx.defaultSettings.previewMaxChars),
        minChars: Number(current.previewMinChars || ctx.defaultSettings.previewMinChars),
      };
    }

    function isGenerationControlNode(node) {
      if (!node || node.nodeType !== 1) return false;
      return node.matches(RESPONSE_GENERATING_SELECTOR)
        || Boolean(node.querySelector(RESPONSE_GENERATING_SELECTOR));
    }

    function ensureController() {
      if (autoSpeechController) return autoSpeechController;
      if (!assistantText || !autoSpeech || typeof autoSpeech.createAutoSpeechController !== 'function') {
        throw new Error('assistant text and auto speech modules must load before content-dom-observer.js');
      }
      autoSpeechController = autoSpeech.createAutoSpeechController({
        sentFlag: AUTO_SENT_FLAG,
        getAssistantNodes,
        extractAssistantText,
        getStableKey,
        isResponseGenerating,
        hasResponseCompletionControl,
        getPreviewOptions,
        splitSpeakChunks,
        extractAutoPreview,
        stableDelayForPreview,
        canFinalizePreview: textCore.canFinalizePreview,
        reportChunks: ctx.reportChunks,
        markResponseCompleted: ctx.markResponseCompleted,
        isAutoEnabled: () => Boolean(ctx.isEnabled() && settings().enabled),
        isGenerationControlNode,
        afterInspectLatest: () => {
          if (settings().micConversationEnabled) void ctx.ensureLiveController()?.inspect();
        },
      });
      return autoSpeechController;
    }

    return {
      getAssistantNodes,
      getStableKey,
      extractAssistantText,
      isResponseGenerating,
      markExistingMessagesAsSeen: () => ensureController().markExistingMessagesAsSeen(),
      rebaseline: () => ensureController().rebaseline(),
      reportLatestSnapshot: () => ensureController().reportLatestSnapshot(),
      scheduleInspect: (mutations = []) => ensureController().scheduleInspect(mutations),
    };
  }

  global.LocalVoiceContentDomObserver = Object.freeze({ create });
})(globalThis);
