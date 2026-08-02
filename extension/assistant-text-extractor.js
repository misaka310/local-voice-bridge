'use strict';

(function exposeAssistantTextExtractor(root, factory) {
  const api = factory(root && root.ContentTextCore);
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceAssistantText = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, (textCore) => {
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
    return normalizeText(value).toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function sourceHint(element) {
    if (!element || typeof element.getAttribute !== 'function') return false;
    const hint = ['data-testid', 'aria-label', 'role']
      .map((name) => String(element.getAttribute(name) || ''))
      .join(' ')
      .toLowerCase();
    return /(?:citation|source|attribution)/.test(hint);
  }

  function isBareHostLabel(link, label) {
    if (!label || label.length > 40 || !link || typeof link.getAttribute !== 'function') return false;
    const href = String(link.getAttribute('href') || '').trim();
    if (!href) return false;
    try {
      const parsed = new URL(href, 'https://chatgpt.com/');
      if (!/^https?:$/.test(parsed.protocol)) return false;
      const host = parsed.hostname.toLowerCase().replace(/^www\./, '');
      const hostParts = host.split('.').filter(Boolean);
      const candidates = new Set([
        hostParts[0] || '',
        hostParts.slice(0, -1).join(''),
        host.replace(/\./g, ''),
      ].map(normalizedLinkLabel).filter(Boolean));
      if (candidates.has(normalizedLinkLabel(label))) return true;
      const labelTokens = normalizeText(label).toLowerCase().match(/[a-z0-9]+/g) || [];
      const hostTokens = new Set(host.split(/[^a-z0-9]+/).filter(Boolean));
      return labelTokens.length >= 2 && labelTokens.every((token) => hostTokens.has(token));
    } catch (_error) {
      return false;
    }
  }

  function sourceDecorationRemainder(element, labels) {
    let text = normalizeText(element && (element.innerText || element.textContent || ''));
    for (const label of [...new Set(labels)].sort((a, b) => b.length - a.length)) {
      const pattern = label
        .split(/\s+/)
        .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, (character) => `\\${character}`))
        .join('\\s*');
      text = text.replace(new RegExp(pattern, 'gi'), ' ');
    }
    return text
      .replace(/[+＋·•]\s*\d+/g, ' ')
      .replace(/\d+\s*(?:sources?|citations?|件|個)/gi, ' ')
      .replace(/[+＋·•|｜]/g, ' ')
      .replace(/\s+/g, '');
  }

  function decorativeSourceContainer(link, clone, isSourceLink) {
    let best = link;
    let current = link;
    for (let depth = 0; current && current !== clone && depth < 6; depth += 1) {
      const anchors = typeof current.querySelectorAll === 'function'
        ? Array.from(current.querySelectorAll('a'))
        : [];
      const labels = anchors
        .map((item) => normalizeText(item.innerText || item.textContent || ''))
        .filter(Boolean);
      if (anchors.some(isSourceLink) && !sourceDecorationRemainder(current, labels)) best = current;
      current = current.parentElement;
    }
    return best;
  }

  function removeDecorativeSourceLinks(clone) {
    if (!clone || typeof clone.querySelectorAll !== 'function') return;
    const links = Array.from(clone.querySelectorAll('a'));
    const labelCounts = new Map();
    for (const link of links) {
      const label = normalizeText(link.innerText || link.textContent || '');
      const key = normalizedLinkLabel(label);
      if (key) labelCounts.set(key, (labelCounts.get(key) || 0) + 1);
    }
    for (const link of links) {
      const label = normalizeText(link.innerText || link.textContent || '');
      const key = normalizedLinkLabel(label);
      const repeatedBareLabel = /^[A-Za-z][A-Za-z0-9 ._+-]{1,39}$/.test(label)
        && Number(labelCounts.get(key) || 0) >= 3;
      link.__localVoiceSourceDecoration = Boolean(
        repeatedBareLabel
        || isBareHostLabel(link, label)
        || sourceHint(link)
        || sourceHint(link.parentElement),
      );
    }
    const isSourceLink = (link) => Boolean(link && link.__localVoiceSourceDecoration);
    const removals = new Set(links
      .filter(isSourceLink)
      .map((link) => decorativeSourceContainer(link, clone, isSourceLink)));
    for (const item of removals) item.remove();
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
