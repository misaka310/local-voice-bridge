'use strict';

(function initContentTextCore(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.ContentTextCore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  function normalizePart(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function isJapaneseBoundary(value, side) {
    const text = String(value || '');
    const char = side === 'start' ? text[0] : text[text.length - 1];
    return Boolean(char && /[\u3040-\u30ff\u3400-\u9fff々〆ヵヶー]/.test(char));
  }

  function joinSpeechParts(left, right) {
    const a = normalizePart(left);
    const b = normalizePart(right);
    if (!a) return b;
    if (!b) return a;
    return a.length === 1 && isJapaneseBoundary(a, 'end') && isJapaneseBoundary(b, 'start')
      ? `${a}${b}`
      : `${a} ${b}`;
  }

  function hasTerminalPunctuation(value) {
    return /[。！？!?…]$/.test(normalizePart(value));
  }

  function coalesceOrphanLines(lines, options = {}) {
    const source = Array.isArray(lines) ? lines.map(normalizePart).filter(Boolean) : [];
    if (source.length < 2) return source;
    const minChars = Math.max(1, Number(options.minChars || 40));
    const orphanLimit = Math.max(2, Math.min(8, Math.floor(minChars / 5)));
    const repaired = [];
    for (let index = 0; index < source.length; index += 1) {
      const current = source[index];
      if (current.length <= orphanLimit && !hasTerminalPunctuation(current) && index + 1 < source.length) {
        repaired.push(joinSpeechParts(current, source[index + 1]));
        index += 1;
      } else {
        repaired.push(current);
      }
    }
    return repaired;
  }

  function stableDelayForPreview(preview, options = {}) {
    const text = normalizePart(preview);
    const minChars = Math.max(1, Number(options.minChars || 40));
    const stableMs = Math.max(100, Number(options.stableMs || 1000));
    if (text.length >= minChars) return stableMs;
    if (text.length <= 2 && !hasTerminalPunctuation(text)) return Math.max(stableMs + 400, 5000);
    if (hasTerminalPunctuation(text)) return Math.max(stableMs + 400, 2200);
    return Math.max(stableMs + 400, 3200);
  }

  function stripRepeatedUiLabels(value) {
    let text = String(value || '');
    text = text.replace(/\b([A-Za-z][A-Za-z0-9._+-]{2,31})(?:\s+\1){2,}\b/gi, ' ');
    text = text.replace(/[A-Za-z][A-Za-z0-9._+-]{8,}/g, (segment) => {
      const normalized = segment.toLowerCase();
      const maxUnitLength = Math.min(32, Math.floor(segment.length / 3));
      for (let unitLength = 3; unitLength <= maxUnitLength; unitLength += 1) {
        const unit = normalized.slice(0, unitLength);
        let offset = unitLength;
        let count = 1;
        while (normalized.slice(offset, offset + unitLength) === unit) {
          count += 1;
          offset += unitLength;
        }
        if (count >= 3) return segment.slice(offset);
      }
      return segment;
    });
    return text
      .replace(/[ \t]+\n/g, '\n')
      .replace(/[ \t]{2,}/g, ' ')
      .trim();
  }

  return {
    coalesceOrphanLines,
    hasTerminalPunctuation,
    joinSpeechParts,
    stableDelayForPreview,
    stripRepeatedUiLabels,
  };
});
