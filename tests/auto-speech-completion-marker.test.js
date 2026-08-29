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

function createHarness() {
  const clock = fakeClock();
  const reports = [];
  let completionMarks = 0;
  const node = {
    key: 'auto-off-complete',
    text: 'Autoを無効にしていても回答完了通知は必要です。',
    complete: true,
    dataset: {},
  };
  const controller = createAutoSpeechController({
    sentFlag: 'sent',
    getAssistantNodes: () => [node],
    extractAssistantText: (target) => target.text,
    getStableKey: (target) => target.key,
    isResponseGenerating: () => false,
    hasResponseCompletionControl: (target) => Boolean(target.complete),
    getPreviewOptions: () => ({ maxLines: 2, maxChars: 80, minChars: 10 }),
    splitSpeakChunks: (text) => [text],
    extractAutoPreview: (text) => text,
    stableDelayForPreview: () => 100,
    reportChunks: (entry, isAuto) => { reports.push({ entry, isAuto }); },
    markResponseCompleted: () => { completionMarks += 1; },
    requestRecheck: () => {},
    isAutoEnabled: () => false,
    isGenerationControlNode: () => false,
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    completionEvidenceStableMs: 100,
  });
  return { clock, completionMarks: () => completionMarks, controller, node, reports };
}

test('completed reply marks completion while Auto is off without queueing speech', () => {
  const harness = createHarness();

  harness.controller.processNode(harness.node);
  harness.clock.advance(200);

  assert.equal(harness.completionMarks(), 1);
  assert.equal(harness.reports.length, 0);
  assert.equal(harness.node.dataset.sent, undefined);
});
