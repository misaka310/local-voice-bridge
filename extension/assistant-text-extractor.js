'use strict';

(function exposeAssistantTextExtractor(root, factory) {
  const api = factory(root && root.ContentTextCore, root && root.LocalVoiceAssistantSourceFilter);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceAssistantText = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, (textCore, sourceFilter) => {
  if (!sourceFilter) throw new Error('assistant-source-filter.js must load before assistant-text-extractor.js');

  const REMOVED_CONTENT_SELECTOR = [
    'pre',
    'button',
    'svg',
    'menu',
    'nav',
    'script',
    'style',
    'textarea',
    'input',
    'select',
    'sup',
  ].join(',');

  function normalizeText(text) {
    return String(text || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function isTransientAssistantStatus(text) {
    const normalized = normalizeText(text).replace(/\s+/g, '');
    return /^(?:(?:\d+|個の)?画像を(?:分析|解析)(?:中|しています)|思考中|考え中|Thinking|Analyzing(?:the)?images?)(?:ストリーミングが中断されました。?完全なメッセージを待機しています)?(?:[.…。・]+)?$/i.test(normalized);
  }

  function normalizeMarkdownLine(line) {
    return String(line || '')
      .replace(/^>\s*/g, '')
      .replace(/^#{1,6}\s*/g, '')
      .replace(/^\s*[-*+]\s+/g, '')
      .replace(/^\s*\d+\.\s+/g, '')
      .replace(/\[(.*?)\]\((.*?)\)/g, '$1')
      .replace(/[*_~`]/g, '')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function normalizedLinkLabel(value) {
    return sourceFilter.normalizedLinkLabel(value, normalizeText);
  }

  function isBareHostLabel(link, label) {
    return sourceFilter.isBareHostLabel(link, label, normalizeText);
  }

  function removeDecorativeSourceLinks(clone) {
    sourceFilter.removeDecorativeSourceLinks(clone, normalizeText);
  }

  function extractAssistantText(node) {
    if (!node || typeof node.cloneNode !== 'function') return '';
    const clone = node.cloneNode(true);
    if (typeof clone.querySelectorAll === 'function') {
      clone.querySelectorAll(REMOVED_CONTENT_SELECTOR).forEach((item) => item.remove());
    }
    removeDecorativeSourceLinks(clone);
    const raw = normalizeText(clone.innerText || clone.textContent || '');
    const stripped = textCore && typeof textCore.stripRepeatedUiLabels === 'function'
      ? textCore.stripRepeatedUiLabels(raw)
      : raw;
    return isTransientAssistantStatus(stripped) ? '' : stripped;
  }

  function getAssistantNodes(documentObject) {
    if (!documentObject || typeof documentObject.querySelectorAll !== 'function') return [];
    const primary = Array.from(documentObject.querySelectorAll('[data-message-author-role="assistant"]'));
    if (primary.length > 0) return primary;
    return Array.from(documentObject.querySelectorAll('article')).filter((node) => {
      const label = `${node.getAttribute('aria-label') || ''} ${node.textContent || ''}`.toLowerCase();
      return label.includes('assistant') || label.includes('chatgpt');
    });
  }

  function getStableKey(node, createId = null) {
    if (!node) return '';
    const turn = typeof node.closest === 'function'
      ? node.closest('[data-testid^="conversation-turn-"]')
      : null;
    const testId = turn && typeof turn.getAttribute === 'function' ? turn.getAttribute('data-testid') : '';
    const messageId = (typeof node.getAttribute === 'function' && node.getAttribute('data-message-id'))
      || (node.dataset && node.dataset.messageId);
    if (messageId) return String(messageId);
    if (testId) return String(testId);
    if (!node.__localVoiceBridgeId) {
      node.__localVoiceBridgeId = typeof createId === 'function'
        ? String(createId())
        : `node-${Math.random().toString(36).slice(2)}`;
    }
    return node.__localVoiceBridgeId;
  }

  return {
    extractAssistantText,
    getAssistantNodes,
    getStableKey,
    isBareHostLabel,
    isTransientAssistantStatus,
    normalizeMarkdownLine,
    normalizeText,
    normalizedLinkLabel,
    removeDecorativeSourceLinks,
  };
}));
