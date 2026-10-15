'use strict';

(function exposeLiveBrowserCore(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LocalVoiceLiveBrowser = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const TERMINAL_PATTERN = /[。！？!?](?:[」』】）)\]”’"']*)$/u;

  function normalizeText(value) {
    return String(value || '')
      .replace(/\u00a0/g, ' ')
      .replace(/\r\n?/g, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  function conversationKey(value) {
    try {
      const url = new URL(String(value || ''));
      const path = url.pathname.replace(/\/+$/, '') || '/';
      return `${url.origin}${path}`;
    } catch (_error) {
      return String(value || '').split(/[?#]/, 1)[0].replace(/\/+$/, '');
    }
  }

  function randomId(prefix = 'id', cryptoObject = globalThis.crypto) {
    if (cryptoObject && typeof cryptoObject.randomUUID === 'function') {
      return `${prefix}-${cryptoObject.randomUUID()}`;
    }
    if (cryptoObject && typeof cryptoObject.getRandomValues === 'function') {
      const bytes = new Uint8Array(16);
      cryptoObject.getRandomValues(bytes);
      return `${prefix}-${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`;
    }
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  }

  async function sha256Text(value, cryptoObject = globalThis.crypto) {
    const text = String(value || '');
    if (!cryptoObject || !cryptoObject.subtle || typeof cryptoObject.subtle.digest !== 'function') {
      let hash = 2166136261;
      for (let index = 0; index < text.length; index += 1) {
        hash ^= text.charCodeAt(index);
        hash = Math.imul(hash, 16777619);
      }
      return `fallback-${(hash >>> 0).toString(16).padStart(8, '0')}`;
    }
    const encoder = typeof TextEncoder !== 'undefined' ? new TextEncoder() : null;
    if (!encoder) throw new Error('TextEncoder is unavailable');
    const digest = await cryptoObject.subtle.digest('SHA-256', encoder.encode(text));
    return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('');
  }

  function assistantBaseline(nodes, getKey) {
    const list = Array.from(nodes || []);
    const keys = list.map((node, index) => String(getKey(node, index) || '')).filter(Boolean);
    return {
      count: list.length,
      keys,
      lastKey: keys.length ? keys[keys.length - 1] : '',
    };
  }

  function resolveAssistantBinding({ baselineKeys = [], candidates = [], getKey }) {
    const baseline = new Set(Array.from(baselineKeys || [], (value) => String(value || '')).filter(Boolean));
    const unique = [];
    const seen = new Set();
    for (let index = 0; index < candidates.length; index += 1) {
      const node = candidates[index];
      const key = String(getKey(node, index) || '');
      if (!key || baseline.has(key) || seen.has(key)) continue;
      seen.add(key);
      unique.push({ node, key });
    }
    if (unique.length === 1) return { ok: true, candidateCount: 1, ...unique[0] };
    return {
      ok: false,
      candidateCount: unique.length,
      reason: unique.length ? 'assistant-binding-ambiguous' : 'assistant-binding-pending',
    };
  }

  function stripUnsafeStreamingRegions(value) {
    const lines = normalizeText(value).split('\n');
    const safe = [];
    let inFence = false;
    for (const sourceLine of lines) {
      const line = sourceLine.trim();
      if (/^```/.test(line)) {
        inFence = !inFence;
        continue;
      }
      if (inFence || !line) continue;
      if (/^(?:https?:\/\/|www\.)\S+$/i.test(line)) continue;
      safe.push(line.replace(/`[^`\n]*`/g, '').trim());
    }
    return safe.filter(Boolean).join('\n');
  }

  function sentenceParts(value, { isFinal = false } = {}) {
    const text = stripUnsafeStreamingRegions(value);
    const parts = [];
    let current = '';
    let bracketDepth = 0;
    let quoteDepth = 0;
    const openings = new Set(['（', '(', '［', '[', '【', '{']);
    const closings = new Set(['）', ')', '］', ']', '】', '}']);
    const quotes = new Set(['「', '」', '『', '』', '“', '”', '‘', '’', '"', "'"]);
    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      current += character;
      if (openings.has(character)) bracketDepth += 1;
      else if (closings.has(character)) bracketDepth = Math.max(0, bracketDepth - 1);
      if (quotes.has(character)) quoteDepth = quoteDepth ? 0 : 1;
      const terminal = /[。！？!?]/u.test(character);
      const newline = character === '\n';
      if ((terminal && bracketDepth === 0) || (newline && bracketDepth === 0 && quoteDepth === 0)) {
        const sentence = normalizeText(current);
        if (sentence && (terminal || isFinal)) parts.push(sentence);
        current = '';
      }
    }
    const tail = normalizeText(current);
    if (isFinal && tail) parts.push(tail);
    return parts;
  }

  function splitStableSentences(value, options = {}) {
    const maxChars = Math.max(20, Math.min(200, Number(options.maxChars) || 80));
    const minChars = Math.max(1, Math.min(maxChars, Number(options.minChars) || 8));
    const isFinal = Boolean(options.isFinal);
    const sentences = sentenceParts(value, { isFinal });
    const chunks = [];
    let buffer = '';
    const pushBuffer = () => {
      const normalized = normalizeText(buffer);
      if (normalized) chunks.push(normalized);
      buffer = '';
    };
    for (const sentence of sentences) {
      if (!sentence) continue;
      if (sentence.length > maxChars) {
        pushBuffer();
        let remaining = sentence;
        while (remaining.length > maxChars) {
          let splitAt = remaining.lastIndexOf('、', maxChars);
          if (splitAt < minChars) splitAt = remaining.lastIndexOf(' ', maxChars);
          if (splitAt < minChars) splitAt = maxChars;
          chunks.push(normalizeText(remaining.slice(0, splitAt + (splitAt < maxChars ? 1 : 0))));
          remaining = normalizeText(remaining.slice(splitAt + (splitAt < maxChars ? 1 : 0)));
        }
        if (remaining) buffer = remaining;
        continue;
      }
      const combined = normalizeText(buffer ? `${buffer} ${sentence}` : sentence);
      if (buffer && combined.length > maxChars) pushBuffer();
      buffer = normalizeText(buffer ? `${buffer} ${sentence}` : sentence);
      if (buffer.length >= minChars && TERMINAL_PATTERN.test(buffer)) pushBuffer();
    }
    if (isFinal) pushBuffer();
    return chunks.filter(Boolean);
  }

  function newChunks(previousChunks, nextChunks) {
    const previous = Array.from(previousChunks || [], (value) => normalizeText(value));
    const next = Array.from(nextChunks || [], (value) => normalizeText(value));
    let prefix = 0;
    while (prefix < previous.length && prefix < next.length && previous[prefix] === next[prefix]) prefix += 1;
    if (prefix < previous.length) return { ok: false, reason: 'stream-prefix-changed', chunks: [] };
    return { ok: true, chunks: next.slice(prefix), allChunks: next };
  }

  async function boundedRetry(operation, options = {}) {
    const maxAttempts = Math.max(1, Math.min(8, Number(options.maxAttempts) || 4));
    const sleep = typeof options.sleep === 'function'
      ? options.sleep
      : (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
    const isCurrent = typeof options.isCurrent === 'function' ? options.isCurrent : () => true;
    let attempt = 0;
    while (attempt < maxAttempts) {
      if (!isCurrent()) return { ok: false, cancelled: true, attempts: attempt };
      attempt += 1;
      const result = await operation(attempt);
      if (!result || result.retry !== true) return { ...result, attempts: attempt };
      if (attempt >= maxAttempts) return { ...result, exhausted: true, attempts: attempt };
      const retryAfterMs = Math.max(25, Math.min(2000, Number(result.retryAfterMs) || 100));
      await sleep(retryAfterMs);
    }
    return { ok: false, exhausted: true, attempts: attempt };
  }

  return {
    normalizeText,
    conversationKey,
    randomId,
    sha256Text,
    assistantBaseline,
    resolveAssistantBinding,
    splitStableSentences,
    newChunks,
    boundedRetry,
  };
}));
