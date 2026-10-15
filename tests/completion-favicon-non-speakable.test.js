'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { createAutoSpeechController } = require('../extension/auto-speech-controller.js');

function fakeClock() {
  let current = 0;
  let sequence = 0;
  const timers = new Map();
  function setTimeout(callback, delay) {
    const id = ++sequence;
    timers.set(id, { callback, at: current + Math.max(0, Number(delay) || 0) });
    return id;
  }
  function clearTimeout(id) {
    timers.delete(id);
  }
  function advance(milliseconds) {
    const target = current + milliseconds;
    while (true) {
      const next = Array.from(timers.entries())
        .filter(([, timer]) => timer.at <= target)
        .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      const [id, timer] = next;
      timers.delete(id);
      current = timer.at;
      timer.callback();
    }
    current = target;
  }
  return { now: () => current, setTimeout, clearTimeout, advance };
}

test('completed non-speakable reply still marks the tab complete without queueing speech', () => {
  const clock = fakeClock();
  const node = {
    key: 'code-only-complete',
    text: '```js\nconst answer = 42;\n```',
    dataset: {},
    complete: true,
  };
  let completionMarks = 0;
  const reports = [];

  const controller = createAutoSpeechController({
    sentFlag: 'sent',
    getAssistantNodes: () => [node],
    extractAssistantText: (candidate) => candidate.text,
    getStableKey: (candidate) => candidate.key,
    isResponseGenerating: () => false,
    hasResponseCompletionControl: (candidate) => Boolean(candidate.complete),
    getPreviewOptions: () => ({ maxLines: 2, maxChars: 80, minChars: 10 }),
    splitSpeakChunks: () => [],
    extractAutoPreview: () => '',
    stableDelayForPreview: () => 100,
    reportChunks: (entry, isAuto) => { reports.push({ entry, isAuto }); },
    markResponseCompleted: () => { completionMarks += 1; },
    requestRecheck: () => {},
    isAutoEnabled: () => true,
    isGenerationControlNode: () => false,
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    inspectDelayMs: 20,
    completionEvidenceStableMs: 100,
    generationRecheckMs: 30000,
  });

  controller.processNode(node);
  clock.advance(250);

  assert.equal(completionMarks, 1);
  assert.equal(reports.length, 0);
  assert.equal(node.dataset.sent, undefined);
});
