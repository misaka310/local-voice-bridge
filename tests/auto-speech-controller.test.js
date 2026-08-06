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
  function pendingDelays() {
    return Array.from(timers.values()).map((timer) => timer.at - current).sort((a, b) => a - b);
  }
  return { now: () => current, setTimeout, clearTimeout, advance, pendingDelays };
}

function createHarness() {
  const clock = fakeClock();
  const nodes = [];
  const reports = [];
  const requestRechecks = [];
  let autoEnabled = true;
  let generating = false;
  let completionMarks = 0;
  const controller = createAutoSpeechController({
    sentFlag: 'sent',
    getAssistantNodes: () => nodes,
    extractAssistantText: (node) => node.text,
    getStableKey: (node) => node.key,
    isResponseGenerating: () => generating,
    hasResponseCompletionControl: (node) => Boolean(node.complete),
    getPreviewOptions: () => ({ maxLines: 2, maxChars: 80, minChars: 10 }),
    splitSpeakChunks: (text) => [text],
    extractAutoPreview: (text) => text,
    stableDelayForPreview: () => 100,
    reportChunks: (entry, isAuto) => { reports.push({ entry, isAuto }); },
    markResponseCompleted: () => { completionMarks += 1; },
    requestRecheck: (delayMs) => { requestRechecks.push(delayMs); },
    isAutoEnabled: () => autoEnabled,
    isGenerationControlNode: (node) => Boolean(node && node.generationControl),
    now: clock.now,
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    inspectDelayMs: 20,
    completionEvidenceStableMs: 100,
    generationRecheckMs: 30000,
  });
  return {
    clock,
    controller,
    nodes,
    reports,
    requestRechecks,
    setAutoEnabled: (value) => { autoEnabled = value; },
    setGenerating: (value) => { generating = value; },
    completionMarks: () => completionMarks,
  };
}

test('baseline marks visible replies as consumed and does not Auto queue later growth', () => {
  const harness = createHarness();
  const node = { key: 'old', text: '既存の返答です。', dataset: {} };
  harness.nodes.push(node);
  harness.controller.markExistingMessagesAsSeen();
  assert.equal(node.dataset.sent, '1');

  node.text = '既存の返答です。後から増えた文章です。';
  harness.controller.processNode(node);
  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].isAuto, false);
});

test('baseline leaves an empty assistant shell eligible for its later completed reply', () => {
  const harness = createHarness();
  const node = { key: 'pending', text: '', dataset: {}, complete: false };
  harness.nodes.push(node);
  harness.controller.markExistingMessagesAsSeen();
  assert.equal(node.dataset.sent, undefined);

  node.text = 'Auto反映後に完成した新しい返答です。';
  node.complete = true;
  harness.controller.processNode(node);
  harness.clock.advance(200);

  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].isAuto, true);
});

test('reply shell observed while Auto is off remains eligible when it completes as Auto turns on', () => {
  const harness = createHarness();
  const node = { key: 'activation-race', text: '', dataset: {}, complete: false };
  harness.nodes.push(node);
  harness.setAutoEnabled(false);
  harness.controller.processNode(node);

  node.text = 'Auto切替と同時に完成した新しい返答です。';
  node.complete = true;
  harness.controller.processNode(node);
  assert.equal(harness.reports.length, 0);

  harness.setAutoEnabled(true);
  harness.controller.markExistingMessagesAsSeen();
  harness.controller.processNode(node);
  harness.clock.advance(200);

  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].isAuto, true);
});

test('new completed reply queues exactly one Auto preview and one completion marker', () => {
  const harness = createHarness();
  const node = { key: 'new', text: '十分な長さを持つ新しい返答です。', dataset: {}, complete: true };
  harness.nodes.push(node);
  harness.controller.processNode(node);
  harness.clock.advance(200);

  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].isAuto, true);
  assert.equal(harness.reports[0].entry.messageKey, 'new');
  assert.equal(harness.reports[0].entry.completionReason, 'action-control');
  assert.equal(node.dataset.sent, '1');
  assert.equal(harness.completionMarks(), 1);
  harness.controller.processNode(node);
  assert.equal(harness.completionMarks(), 1);
});

test('short streaming fragment waits for stable completion evidence before Auto', () => {
  const harness = createHarness();
  const node = { key: 'short', text: '途中', dataset: {}, complete: false };
  harness.nodes.push(node);
  harness.setGenerating(true);
  harness.controller.processNode(node);
  harness.clock.advance(1000);
  assert.equal(harness.reports.length, 0);

  harness.setGenerating(false);
  node.complete = true;
  harness.controller.processNode(node);
  harness.clock.advance(50);
  assert.equal(harness.reports.length, 0);
  harness.clock.advance(150);
  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].entry.autoPreview, '途中');
  assert.equal(harness.reports[0].entry.completionReason, 'generation-ended-with-action-control');
});

test('long streaming text never queues before the response completion control appears', () => {
  const harness = createHarness();
  const node = {
    key: 'long-stream',
    text: '十分な長さがある生成途中の返答でも、推論完了前には読み上げてはいけません。',
    dataset: {},
    complete: false,
  };
  harness.nodes.push(node);
  harness.setGenerating(true);
  harness.controller.processNode(node);
  harness.clock.advance(5000);
  assert.equal(harness.reports.length, 0);

  harness.setGenerating(false);
  harness.controller.processNode(node);
  harness.clock.advance(5000);
  assert.equal(harness.reports.length, 0);

  node.complete = true;
  harness.controller.processNode(node);
  harness.clock.advance(200);
  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].entry.completionReason, 'generation-ended-with-action-control');
});

test('transient completion evidence for a short prefix is revoked when generation resumes', () => {
  const harness = createHarness();
  const node = { key: 'transient', text: 'うん', dataset: {}, complete: false };
  harness.nodes.push(node);
  harness.setGenerating(true);
  harness.controller.processNode(node);
  harness.clock.advance(200);

  harness.setGenerating(false);
  node.complete = true;
  harness.controller.processNode(node);
  harness.clock.advance(50);
  assert.equal(harness.reports.length, 0);

  harness.setGenerating(true);
  node.complete = false;
  harness.controller.processNode(node);
  harness.clock.advance(1000);
  assert.equal(harness.reports.length, 0);

  node.text = 'うん、局所修正ではなく完了検知の状態機械を直しました。';
  harness.controller.processNode(node);
  harness.setGenerating(false);
  node.complete = true;
  harness.controller.processNode(node);
  harness.clock.advance(200);
  assert.equal(harness.reports.length, 1);
  assert.equal(harness.reports[0].entry.autoPreview, node.text);
});

test('long generation uses one 30-second tab-local fallback without background polling', () => {
  const harness = createHarness();
  const node = { key: 'long-running', text: '長時間の回答を生成しています。', dataset: {}, complete: false };
  harness.nodes.push(node);
  harness.setGenerating(true);

  harness.controller.processNode(node);

  assert.deepEqual(harness.clock.pendingDelays(), [30000]);
  assert.deepEqual(harness.requestRechecks, []);
  harness.clock.advance(29999);
  assert.equal(harness.reports.length, 0);
});

test('mutation scheduling collapses bursts and inspects immediately when generation ends', () => {
  const harness = createHarness();
  const node = { key: 'scheduled', text: '十分な長さを持つ新しい返答です。', dataset: {}, complete: true };
  harness.nodes.push(node);
  harness.controller.scheduleInspect([]);
  harness.controller.scheduleInspect([]);
  harness.clock.advance(19);
  assert.equal(harness.reports.length, 0);
  harness.clock.advance(200);
  assert.equal(harness.reports.length, 1);

  const second = { key: 'ended', text: '生成終了後に確認される返答です。', dataset: {}, complete: true };
  harness.nodes.push(second);
  harness.controller.scheduleInspect([{ removedNodes: [{ generationControl: true }] }]);
  harness.clock.advance(200);
  assert.equal(harness.reports.length, 2);
});
