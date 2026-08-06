'use strict';

(function exposeContentMutationFilter(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceContentMutationFilter = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const ASSISTANT_SELECTOR = '[data-message-author-role="assistant"]';
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
    'button[aria-label="Copy"]',
    'button[aria-label="コピー"]',
  ].join(',');
  const RESPONSE_ERROR_SELECTOR = [
    '[data-testid="response-error"]',
    '[data-testid="conversation-turn-error"]',
    '[data-testid="error-message"]',
    '[data-testid*="response-error"]',
    '[data-testid*="generation-error"]',
  ].join(',');
  const RESPONSE_RETRY_SELECTOR = [
    'button[data-testid*="retry"]',
    'button[data-testid="regenerate-response-button"]',
    'button[aria-label="Retry"]',
    'button[aria-label="Try again"]',
    'button[aria-label="Regenerate"]',
    'button[aria-label="再試行"]',
    'button[aria-label="もう一度試す"]',
    'button[aria-label="再生成"]',
  ].join(',');
  const RESPONSE_ERROR_TEXT = [
    'something went wrong',
    'there was an error generating a response',
    'error generating a response',
    'an error occurred while generating',
    'network error',
    '問題が発生しました',
    '応答の生成中にエラー',
    '回答の生成中にエラー',
    'エラーが発生しました',
  ];

  function elementForNode(node) {
    if (!node) return null;
    if (node.nodeType === 1) return node;
    const parent = node.parentElement || node.parentNode;
    return parent && parent.nodeType === 1 ? parent : null;
  }

  function safeCall(element, method, selector) {
    if (!element || typeof element[method] !== 'function') return method === 'matches' ? false : null;
    try {
      return element[method](selector);
    } catch (_error) {
      return method === 'matches' ? false : null;
    }
  }

  function articleLooksLikeAssistant(article) {
    if (!article) return false;
    const label = `${article.getAttribute?.('aria-label') || ''} ${article.textContent || ''}`.toLowerCase();
    return label.includes('assistant') || label.includes('chatgpt');
  }

  function nodeTouchesAssistant(node) {
    const element = elementForNode(node);
    if (!element) return false;
    if (safeCall(element, 'matches', ASSISTANT_SELECTOR)) return true;
    if (safeCall(element, 'closest', ASSISTANT_SELECTOR)) return true;
    if (safeCall(element, 'querySelector', ASSISTANT_SELECTOR)) return true;
    const article = safeCall(element, 'matches', 'article') ? element : safeCall(element, 'closest', 'article');
    return articleLooksLikeAssistant(article);
  }

  function nodeTouchesResponseControl(node) {
    const element = elementForNode(node);
    if (!element) return false;
    return [
      RESPONSE_GENERATING_SELECTOR,
      RESPONSE_COMPLETE_SELECTOR,
      RESPONSE_ERROR_SELECTOR,
      RESPONSE_RETRY_SELECTOR,
      '[role="alert"]',
    ].some((selector) => (
      safeCall(element, 'matches', selector)
      || safeCall(element, 'closest', selector)
      || safeCall(element, 'querySelector', selector)
    ));
  }

  function looksLikeResponseErrorText(node) {
    const text = String(node?.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
    return Boolean(text && text.length <= 600 && RESPONSE_ERROR_TEXT.some((pattern) => text.includes(pattern)));
  }

  function isResponseError(document, assistantNodes = []) {
    if (safeCall(document, 'querySelector', RESPONSE_ERROR_SELECTOR)) return true;
    const alerts = Array.from(safeCall(document, 'querySelectorAll', '[role="alert"]') || []);
    if (alerts.some(looksLikeResponseErrorText)) return true;
    const latest = Array.from(assistantNodes || []).at(-1);
    if (!latest || !looksLikeResponseErrorText(latest)) return false;
    const turn = safeCall(latest, 'closest', '[data-testid^="conversation-turn-"]') || latest;
    const retry = safeCall(turn, 'querySelector', RESPONSE_RETRY_SELECTOR);
    const normalCompletion = safeCall(turn, 'querySelector', RESPONSE_COMPLETE_SELECTOR);
    return Boolean(retry && !normalCompletion);
  }

  function isRelevantMutationBatch(mutations = []) {
    const batch = Array.from(mutations || []);
    if (!batch.length) return true;
    return batch.some((mutation) => {
      if (nodeTouchesAssistant(mutation.target) || nodeTouchesResponseControl(mutation.target)) return true;
      const changedNodes = [
        ...Array.from(mutation.addedNodes || []),
        ...Array.from(mutation.removedNodes || []),
      ];
      return changedNodes.some((node) => nodeTouchesAssistant(node) || nodeTouchesResponseControl(node));
    });
  }

  return Object.freeze({
    ASSISTANT_SELECTOR,
    RESPONSE_GENERATING_SELECTOR,
    RESPONSE_COMPLETE_SELECTOR,
    RESPONSE_ERROR_SELECTOR,
    RESPONSE_RETRY_SELECTOR,
    isResponseError,
    isRelevantMutationBatch,
  });
}));
