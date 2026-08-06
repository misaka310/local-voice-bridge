'use strict';

(function initContentDomObserver(global) {
  const AUTO_SENT_FLAG = 'localVoiceSent';
  const MESSAGE_SELECTOR = '[data-message-author-role="user"], [data-message-author-role="assistant"]';
  const mutationFilter = global.LocalVoiceContentMutationFilter;
  if (!mutationFilter) throw new Error('content-mutation-filter.js must load before content-dom-observer.js');
  const { RESPONSE_GENERATING_SELECTOR, RESPONSE_COMPLETE_SELECTOR } = mutationFilter;

  function create(ctx) {
    const textCore = global.ContentTextCore;
    const assistantText = global.LocalVoiceAssistantText;
    const autoSpeech = global.LocalVoiceAutoSpeech;
    let autoSpeechController = null;
    let newConversation = false;
    const location = ctx.location;

    function settings() {
      return ctx.getSettings();
    }

    function isBlankNewConversation() {
      if (location.pathname !== '/') return false;
      return !ctx.document.querySelector(MESSAGE_SELECTOR);
    }

    function nodeContainsConversationMessage(node) {
      if (!node) return false;
      const element = node.nodeType === 1 ? node : node.parentElement || node.parentNode;
      if (!element || element.nodeType !== 1) return false;
      if (element.matches?.('[data-message-author-role]')) return true;
      if (element.closest?.('[data-message-author-role]')) return true;
      return Boolean(element.querySelector?.('[data-message-author-role]'));
    }

    function newConversationMutationNeedsRefresh(mutations = []) {
      if (location.pathname !== '/') return newConversation;
      if (!newConversation || !mutations.length) return true;
      return Array.from(mutations).some((mutation) => (
        nodeContainsConversationMessage(mutation.target)
        || [...Array.from(mutation.addedNodes || []), ...Array.from(mutation.removedNodes || [])]
          .some(nodeContainsConversationMessage)
      ));
    }

    function updateNewConversationStatus(mutations = []) {
      if (!newConversationMutationNeedsRefresh(mutations)) return false;
      const next = isBlankNewConversation();
      if (next === newConversation) return false;
      newConversation = next;
      ctx.setNewConversation(next);
      return true;
    }

    function normalizeText(text) {
      return assistantText.normalizeText(text);
    }

    function isResponseGenerating() {
      return Boolean(ctx.document.querySelector(RESPONSE_GENERATING_SELECTOR));
    }

    function isResponseError() {
      return mutationFilter.isResponseError(ctx.document, getAssistantNodes());
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

    function isCompletionControlNode(node) {
      if (!node || node.nodeType !== 1) return false;
      return node.matches(RESPONSE_COMPLETE_SELECTOR)
        || Boolean(node.querySelector(RESPONSE_COMPLETE_SELECTOR));
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
        reportChunks: ctx.reportChunks,
        markResponseCompleted: ctx.markResponseCompleted,
        isAutoEnabled: () => Boolean(ctx.isEnabled() && settings().enabled),
        isGenerationControlNode,
        isCompletionControlNode,
        requestRecheck: (delayMs) => ctx.requestAutoRecheck(delayMs),
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
      inspectLatestAssistant: () => {
        updateNewConversationStatus();
        if (isResponseGenerating()) ctx.markResponseGenerating();
        else if (isResponseError()) {
          ctx.markResponseError();
          return false;
        }
        return ensureController().inspectLatestAssistant();
      },
      scheduleInspect: (mutations = []) => {
        updateNewConversationStatus(mutations);
        if (!mutationFilter.isRelevantMutationBatch(mutations)) return false;
        if (isResponseGenerating()) ctx.markResponseGenerating();
        else if (isResponseError()) {
          ctx.markResponseError();
          return false;
        }
        return ensureController().scheduleInspect(mutations);
      },
    };
  }

  global.LocalVoiceContentDomObserver = Object.freeze({ create });
})(globalThis);
