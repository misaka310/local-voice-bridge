'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const fc = require('fast-check');
const textCore = require('../extension/content-text-core.js');
globalThis.ContentTextCore = textCore;
globalThis.LocalVoiceAssistantSourceFilter = require('../extension/assistant-source-filter.js');
const assistantText = require('../extension/assistant-text-extractor.js');

test('property: assistant text normalization is idempotent and removes CR characters', () => {
  fc.assert(
    fc.property(fc.string(), (raw) => {
      const normalized = assistantText.normalizeText(raw);
      assert.equal(assistantText.normalizeText(normalized), normalized);
      assert.equal(normalized.includes('\r'), false);
      assert.equal(typeof assistantText.isTransientAssistantStatus(raw), 'boolean');
    }),
    { numRuns: 1000 },
  );
});
