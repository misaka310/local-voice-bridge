'use strict';

(function exposeAssistantSourceFilter(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceAssistantSourceFilter = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  function fallbackNormalizeText(text) {
    return String(text || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function normalize(value, normalizeText) {
    return (typeof normalizeText === 'function' ? normalizeText : fallbackNormalizeText)(value);
  }

  function normalizedLinkLabel(value, normalizeText) {
    return normalize(value, normalizeText).toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  function sourceHint(element) {
    if (!element || typeof element.getAttribute !== 'function') return false;
    const uiTokens = ['chip', 'card', 'pill', 'group', 'reference', 'references'];
    for (const name of ['data-testid', 'aria-label', 'role']) {
      const tokens = alphaNumericTokens(element.getAttribute(name) || '');
      if (tokens.some((token) => ['citation', 'citations', 'attribution', 'attributions'].includes(token))) return true;
      const hasSource = tokens.includes('source') || tokens.includes('sources');
      if (hasSource && (tokens.length === 1 || tokens.some((token) => uiTokens.includes(token)))) return true;
    }
    const classTokens = alphaNumericTokens(element.getAttribute('class') || '');
    if (classTokens.some((token) => ['citation', 'citations', 'attribution', 'attributions'].includes(token))) return true;
    const sourceClass = classTokens.includes('source') || classTokens.includes('sources');
    return sourceClass && classTokens.some((token) => uiTokens.includes(token));
  }

  function hasSourceCount(text, normalizeText) {
    const normalized = normalize(text, normalizeText);
    return /(?:[+＋·•]\s*\d+(?![A-Za-z0-9])|\d+\s*(?:sources?|citations?|件|個))/i.test(normalized);
  }

  function hasCompactSourceContext(link, normalizeText) {
    let current = link && link.parentElement;
    for (let depth = 0; current && depth < 3; depth += 1) {
      if (sourceHint(current)) return true;
      const anchors = typeof current.querySelectorAll === 'function'
        ? Array.from(current.querySelectorAll('a'))
        : [];
      const text = normalize(current.innerText || current.textContent || '', normalizeText);
      if (hasSourceCount(text, normalizeText)) return true;
      if (typeof current.matches === 'function' && current.matches('p, li, blockquote, pre, code, td, th')) return false;
      if (anchors.length >= 2) {
        const labels = anchors
          .map((item) => normalize(item.innerText || item.textContent || '', normalizeText))
          .filter(Boolean);
        return !sourceDecorationRemainder(current, labels, normalizeText);
      }
      current = current.parentElement;
    }
    return false;
  }

  function isAsciiProviderLabel(value, normalizeText) {
    const text = normalize(value, normalizeText);
    if (!text || text.length > 40) return false;
    for (const character of text) {
      if (!/[A-Za-z0-9 ._+-]/.test(character)) return false;
    }
    return true;
  }

  function alphaNumericTokens(value) {
    const tokens = [];
    let current = '';
    for (const character of String(value || '').toLowerCase()) {
      const code = character.charCodeAt(0);
      const isLetter = code >= 97 && code <= 122;
      const isDigit = code >= 48 && code <= 57;
      if (isLetter || isDigit) current += character;
      else if (current) {
        tokens.push(current);
        current = '';
      }
    }
    if (current) tokens.push(current);
    return tokens;
  }

  function parseHttpHost(href) {
    try {
      const parsed = new URL(href);
      if (!/^https?:$/.test(parsed.protocol)) return '';
      return parsed.hostname.toLowerCase().replace(/^www\./, '');
    } catch (_error) {
      return '';
    }
  }

  function isBareHostLabel(link, label, normalizeText) {
    const visibleLabel = normalize(label, normalizeText);
    if (!isAsciiProviderLabel(visibleLabel, normalizeText)) return false;
    if (!link || typeof link.getAttribute !== 'function') return false;
    const href = String(link.getAttribute('href') || '').trim();
    if (!href) return false;
    const host = parseHttpHost(href);
    if (!host) return false;
    const hostParts = host.split('.').filter(Boolean);
    const candidates = new Set([
      hostParts[0] || '',
      hostParts.slice(0, -1).join(''),
      host.replace(/\./g, ''),
    ].map((value) => normalizedLinkLabel(value, normalizeText)).filter(Boolean));
    if (candidates.has(normalizedLinkLabel(visibleLabel, normalizeText))) return true;
    const labelTokens = alphaNumericTokens(visibleLabel);
    const hostTokens = new Set(alphaNumericTokens(host));
    return labelTokens.length >= 2 && labelTokens.every((token) => hostTokens.has(token));
  }

  function escapeRegex(value) {
    const special = new Set('\\^$.*+?()[]{}|');
    let escaped = '';
    for (const character of String(value || '')) {
      escaped += special.has(character) ? `\\${character}` : character;
    }
    return escaped;
  }

  function sourceDecorationRemainder(element, labels, normalizeText) {
    let text = normalize(element && (element.innerText || element.textContent || ''), normalizeText);
    for (const label of [...new Set(labels)].sort((a, b) => b.length - a.length)) {
      const pattern = label.split(/\s+/).map(escapeRegex).join('\\s*');
      text = text.replace(new RegExp(pattern, 'gi'), ' ');
    }
    return text
      .replace(/[+＋·•]\s*\d+(?![A-Za-z0-9])/g, ' ')
      .replace(/\d+\s*(?:sources?|citations?|件|個)/gi, ' ')
      .replace(/[+＋·•|｜]/g, ' ')
      .replace(/\s+/g, '');
  }

  function decorativeSourceContainer(link, clone, isSourceLink, normalizeText) {
    let best = link;
    let current = link;
    for (let depth = 0; current && current !== clone && depth < 6; depth += 1) {
      const anchors = typeof current.querySelectorAll === 'function'
        ? Array.from(current.querySelectorAll('a'))
        : [];
      const labels = anchors
        .filter(isSourceLink)
        .map((item) => normalize(item.innerText || item.textContent || '', normalizeText))
        .filter(Boolean);
      if (anchors.some(isSourceLink) && !sourceDecorationRemainder(current, labels, normalizeText)) best = current;
      current = current.parentElement;
    }
    return best;
  }

  function removeDecorativeSourceLinks(clone, normalizeText) {
    if (!clone || typeof clone.querySelectorAll !== 'function') return;
    const links = Array.from(clone.querySelectorAll('a'));
    const labelCounts = new Map();
    for (const link of links) {
      const label = normalize(link.innerText || link.textContent || '', normalizeText);
      const key = normalizedLinkLabel(label, normalizeText);
      if (key) labelCounts.set(key, (labelCounts.get(key) || 0) + 1);
    }
    for (const link of links) {
      const label = normalize(link.innerText || link.textContent || '', normalizeText);
      const key = normalizedLinkLabel(label, normalizeText);
      const repeatedBareLabel = /^[A-Za-z][A-Za-z0-9 ._+-]{1,39}$/.test(label)
        && Number(labelCounts.get(key) || 0) >= 3;
      link.__localVoiceSourceDecoration = Boolean(
        repeatedBareLabel
        || (isBareHostLabel(link, label, normalizeText) && hasCompactSourceContext(link, normalizeText))
        || sourceHint(link)
        || sourceHint(link.parentElement),
      );
    }
    const isSourceLink = (link) => Boolean(link && link.__localVoiceSourceDecoration);
    const removals = new Set(links
      .filter(isSourceLink)
      .map((link) => decorativeSourceContainer(link, clone, isSourceLink, normalizeText)));
    for (const item of removals) item.remove();
  }

  return {
    isBareHostLabel,
    normalizedLinkLabel,
    removeDecorativeSourceLinks,
  };
}));
