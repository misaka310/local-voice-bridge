'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

require('../extension/live-browser-core.js');
const { createLiveContentController } = require('../extension/live-content-controller.js');

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

function createHarness() {
  const nodes = [{ key: 'assistant-1', text: 'old' }];
  const calls = [];
  let generating = true;
  let currentLocation = 'https://chatgpt.com/c/current?model=x';
  let activeComposer = null;
  const runtimeMessage = async (type, extra = {}) => {
    calls.push({ type, extra });
    if (type === 'live-submission') {
      const action = extra.action;
      return {
        ok: true,
        sendAllowed: action === 'arm',
        submission: { phase: action === 'arm' ? 'armed' : action === 'commit' ? 'committed' : action === 'bind' ? 'bound' : 'invalidated' },
      };
    }
    if (type === 'live-chunk') return { ok: true, accepted: true, capacity: 1 };
    if (type === 'live-interrupt') return { ok: true, stopping: true, cancelEpoch: Number(extra.payload.cancelEpoch) + 1 };
    if (type === 'live-state') return { submission: { phase: 'idle', current: {} } };
    throw new Error(`unexpected message ${type}`);
  };
  const ids = [];
  const crypto = {
    randomUUID() {
      const value = `uuid-${ids.length + 1}`;
      ids.push(value);
      return value;
    },
    subtle: {
      async digest(_name, bytes) {
        const output = new Uint8Array(32);
        for (let index = 0; index < bytes.length; index += 1) output[index % 32] ^= bytes[index];
        return output.buffer;
      },
    },
  };
  const controller = createLiveContentController({
    getLocation: () => currentLocation,
    getAssistantNodes: () => nodes,
    getStableKey: (node) => node.key,
    extractAssistantText: (node) => node.text,
    isResponseGenerating: () => generating,
    runtimeMessage,
    composerText: (element) => String(element?.value ?? element?.innerText ?? element?.textContent ?? ''),
    composerContainsTarget: (composer, target) => Boolean(composer && target && (
      composer === target || (typeof composer.contains === 'function' && composer.contains(target))
    )),
    resolveComposer: () => activeComposer,
    crypto,
    getVoiceSettings: () => ({ liveTtsProfile: 'speed', referenceVoice: 'suguha', voiceVolume: 0.6 }),
    sleep: async () => {},
  });
  return {
    controller,
    nodes,
    calls,
    setGenerating(value) { generating = value; },
    setLocation(value) { currentLocation = value; },
    setActiveComposer(value) { activeComposer = value; },
  };
}

test('arm is persisted before commit and assistant binding', async () => {
  const harness = createHarness();
  const metadata = harness.controller.metadata('session-1', '質問です');
  const item = {
    ...metadata,
    insertedText: metadata.text,
    syntheticMarker: 'marker-1',
  };

  const arm = await harness.controller.prepareSubmission(item);
  assert.equal(arm.sendAllowed, true);
  await harness.controller.commitSubmission(item);
  harness.nodes.push({ key: 'assistant-2', text: '返答の一文です。まだ途中' });
  await harness.controller.inspect();
  await flush();

  assert.deepEqual(
    harness.calls.filter((call) => call.type === 'live-submission').map((call) => call.extra.action),
    ['arm', 'commit', 'bind'],
  );
  const chunk = harness.calls.find((call) => call.type === 'live-chunk');
  assert.ok(chunk);
  assert.equal(chunk.extra.payload.text, '返答の一文です。');
  assert.equal(chunk.extra.payload.profile, 'speed');
  assert.equal(chunk.extra.payload.referenceVoice, 'suguha');
});

test('ambiguous new assistant messages fail closed and interrupt', async () => {
  const harness = createHarness();
  const item = { ...harness.controller.metadata('session-1', '質問'), insertedText: '質問' };
  await harness.controller.prepareSubmission(item);
  await harness.controller.commitSubmission(item);
  harness.nodes.push({ key: 'assistant-2', text: 'one' }, { key: 'assistant-3', text: 'two' });

  await harness.controller.inspect();

  const interrupt = harness.calls.find((call) => call.type === 'live-interrupt');
  assert.ok(interrupt);
  assert.equal(interrupt.extra.payload.reason, 'assistant-binding-ambiguous');
  assert.equal(harness.calls.some((call) => call.type === 'live-chunk'), false);
});

test('synthetic transcript input does not interrupt but real input does', async () => {
  const harness = createHarness();
  const item = {
    ...harness.controller.metadata('session-1', '質問'),
    insertedText: '質問',
    syntheticMarker: 'marker-1',
  };
  await harness.controller.prepareSubmission(item);

  assert.equal(harness.controller.handleInput({ localVoiceSyntheticInputToken: 'marker-1' }), false);
  assert.equal(harness.controller.handleInput({}), true);
  await flush();

  assert.equal(harness.calls.filter((call) => call.type === 'live-interrupt').length, 1);
  assert.equal(harness.calls.find((call) => call.type === 'live-interrupt').extra.payload.reason, 'composer-input');
});

test('delayed native input for the unchanged inserted transcript does not invalidate the armed submission', async () => {
  const harness = createHarness();
  const composer = { value: '質問' };
  const item = {
    ...harness.controller.metadata('session-1', '質問'),
    insertedText: '質問',
    syntheticMarker: 'marker-1',
    composer,
  };
  await harness.controller.prepareSubmission(item);

  assert.equal(harness.controller.handleInput({ target: composer, isTrusted: true }), false);
  await flush();
  assert.equal(harness.calls.some((call) => call.type === 'live-interrupt'), false);

  composer.value = '質問を変更';
  assert.equal(harness.controller.handleInput({ target: composer, isTrusted: true }), true);
  await flush();
  const interrupt = harness.calls.find((call) => call.type === 'live-interrupt');
  assert.ok(interrupt);
  assert.equal(interrupt.extra.payload.reason, 'composer-input');
});

test('a replacement composer with the unchanged transcript does not invalidate the armed submission', async () => {
  const harness = createHarness();
  const oldComposer = { value: '質問', contains: () => false };
  const replacementChild = {};
  const replacementComposer = {
    value: '質問',
    contains: (target) => target === replacementChild,
  };
  const item = {
    ...harness.controller.metadata('session-1', '質問'),
    insertedText: '質問',
    syntheticMarker: 'marker-1',
    composer: oldComposer,
  };
  await harness.controller.prepareSubmission(item);
  harness.setActiveComposer(replacementComposer);

  assert.equal(harness.controller.handleInput({ target: replacementChild, isTrusted: true }), false);
  await flush();
  assert.equal(harness.calls.some((call) => call.type === 'live-interrupt'), false);

  replacementComposer.value = '質問を変更';
  assert.equal(harness.controller.handleInput({ target: replacementChild, isTrusted: true }), true);
  await flush();
  assert.equal(harness.calls.find((call) => call.type === 'live-interrupt').extra.payload.reason, 'composer-input');
});

test('submit clear event is ignored once but trusted user input still interrupts', async () => {
  const harness = createHarness();
  const item = {
    ...harness.controller.metadata('session-1', '質問'),
    insertedText: '質問',
    syntheticMarker: 'marker-1',
  };
  await harness.controller.prepareSubmission(item);
  const composer = { value: '' };
  assert.equal(harness.controller.markSubmissionClick(item, composer), true);

  assert.equal(harness.controller.handleInput({ target: composer, isTrusted: false }), false);
  assert.equal(harness.controller.handleInput({ target: composer, isTrusted: true }), true);
  await flush();

  const interrupt = harness.calls.find((call) => call.type === 'live-interrupt');
  assert.ok(interrupt);
  assert.equal(interrupt.extra.payload.reason, 'composer-input');
});

test('ChatGPT composer replacement during the automatic send click does not invalidate commit', async () => {
  const harness = createHarness();
  const item = {
    ...harness.controller.metadata('session-1', '質問'),
    insertedText: '質問',
    syntheticMarker: 'marker-1',
  };
  await harness.controller.prepareSubmission(item);
  const originalComposer = { value: '質問' };
  const replacementComposer = { value: '' };
  assert.equal(harness.controller.markSubmissionClick(item, originalComposer), true);

  assert.equal(harness.controller.handleInput({ target: replacementComposer, isTrusted: true }), false);
  await harness.controller.commitSubmission(item);
  await flush();

  assert.deepEqual(
    harness.calls.filter((call) => call.type === 'live-submission').map((call) => call.extra.action),
    ['arm', 'commit'],
  );
  assert.equal(harness.calls.some((call) => call.type === 'live-interrupt'), false);
});

test('final state resubmits the last chunk as final when it was already streamed', async () => {
  const harness = createHarness();
  const item = { ...harness.controller.metadata('session-1', '質問'), insertedText: '質問' };
  await harness.controller.prepareSubmission(item);
  await harness.controller.commitSubmission(item);
  harness.nodes.push({ key: 'assistant-2', text: '最初の返答です。' });
  await harness.controller.inspect();
  await flush();
  harness.setGenerating(false);
  await harness.controller.inspect();
  await flush();

  const chunks = harness.calls.filter((call) => call.type === 'live-chunk');
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].extra.payload.isFinal, false);
  assert.equal(chunks[1].extra.payload.isFinal, true);
  assert.equal(chunks[0].extra.payload.textHash, chunks[1].extra.payload.textHash);
});

test('manual Enter and regenerate pointer both interrupt current live ownership', async () => {
  const first = createHarness();
  const item = { ...first.controller.metadata('session-1', '質問'), insertedText: '質問' };
  await first.controller.prepareSubmission(item);
  assert.equal(first.controller.handleKeydown({ key: 'Enter', shiftKey: false, target: {} }, () => true), true);
  await flush();
  assert.equal(first.calls.find((call) => call.type === 'live-interrupt').extra.payload.reason, 'manual-enter-send');

  const second = createHarness();
  const secondItem = { ...second.controller.metadata('session-2', '質問'), insertedText: '質問' };
  await second.controller.prepareSubmission(secondItem);
  const target = { closest: (selector) => (selector.includes('regenerate-button') ? target : null) };
  assert.equal(second.controller.handlePointer({ target }), true);
  await flush();
  assert.equal(second.calls.find((call) => call.type === 'live-interrupt').extra.payload.reason, 'regenerate');
});

test('same-tab conversation navigation invalidates the old Live ownership', async () => {
  const harness = createHarness();
  const item = { ...harness.controller.metadata('session-1', '質問'), insertedText: '質問' };
  await harness.controller.prepareSubmission(item);
  await harness.controller.commitSubmission(item);
  harness.setLocation('https://chatgpt.com/c/another');

  await harness.controller.inspect();

  const interrupt = harness.calls.find((call) => call.type === 'live-interrupt');
  assert.ok(interrupt);
  assert.equal(interrupt.extra.payload.reason, 'conversation-changed');
  assert.equal(harness.controller.state().phase, 'idle');
});
