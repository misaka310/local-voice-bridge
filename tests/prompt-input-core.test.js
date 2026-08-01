'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const promptInput = require('../extension/prompt-input-core.js');

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.bubbles = Boolean(options.bubbles);
  }
}

class FakeInputEvent extends FakeEvent {}

function createFixture() {
  let documentObject = null;
  const createElement = ({
    tagName = 'DIV',
    attributes = {},
    hidden = false,
    disabled = false,
    form = null,
  } = {}) => {
    const element = {
      tagName,
      hidden,
      inert: false,
      disabled,
      isConnected: true,
      value: '',
      textContent: '',
      innerText: '',
      clicked: 0,
      dispatched: [],
      getAttribute(name) { return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null; },
      closest(selector) {
        if (selector === 'form') return form;
        if (selector === '[hidden], [inert], [aria-hidden="true"]') return hidden ? element : null;
        return null;
      },
      contains(target) { return target === element; },
      focus() { documentObject.activeElement = element; },
      dispatchEvent(event) { element.dispatched.push(event.type); return true; },
      click() { element.clicked += 1; },
    };
    return element;
  };

  const scopedSendButton = createElement({ tagName: 'BUTTON', attributes: { 'aria-label': 'Send prompt' } });
  const form = {
    querySelector(selector) {
      return selector === 'button[aria-label="Send prompt"]' ? scopedSendButton : null;
    },
  };
  const hiddenComposer = createElement({
    tagName: 'TEXTAREA',
    attributes: { placeholder: 'Hidden composer' },
    hidden: true,
  });
  const visibleComposer = createElement({
    tagName: 'DIV',
    attributes: { contenteditable: 'true' },
    form,
  });
  const hiddenGlobalSendButton = createElement({
    tagName: 'BUTTON',
    attributes: { 'aria-label': 'Send prompt' },
    hidden: true,
  });

  const selectorMap = new Map([
    ['#prompt-textarea', [hiddenComposer]],
    ['textarea[data-id="root"]', []],
    ['textarea[placeholder]', [hiddenComposer]],
    ['[contenteditable="true"][data-virtualkeyboard]', []],
    ['div[contenteditable="true"].ProseMirror', [visibleComposer]],
    ['button[data-testid="send-button"]', []],
    ['button[aria-label="Send prompt"]', [hiddenGlobalSendButton]],
    ['button[aria-label*="送信"]', []],
    ['button[aria-label*="Send"]', [hiddenGlobalSendButton]],
  ]);

  documentObject = {
    activeElement: visibleComposer,
    querySelectorAll(selector) { return selectorMap.get(selector) || []; },
    querySelector(selector) { return (selectorMap.get(selector) || [])[0] || null; },
    execCommand() { return false; },
    addEventListener() {},
    removeEventListener() {},
  };

  return {
    documentObject,
    hiddenComposer,
    visibleComposer,
    hiddenGlobalSendButton,
    scopedSendButton,
  };
}

test('findComposer ignores stale hidden composers and keeps the active usable composer', () => {
  const fixture = createFixture();

  const composer = promptInput.findComposer(fixture.documentObject, fixture.visibleComposer);

  assert.equal(composer, fixture.visibleComposer);
  assert.equal(promptInput.isComposerTarget(fixture.documentObject, fixture.visibleComposer), true);
  assert.equal(promptInput.isComposerTarget(fixture.documentObject, fixture.hiddenComposer), false);
});

test('findComposer excludes computed-hidden and read-only composers', () => {
  const fixture = createFixture();
  fixture.hiddenComposer.hidden = false;
  fixture.hiddenComposer.ownerDocument = {
    defaultView: {
      getComputedStyle() { return { display: 'none', visibility: 'visible' }; },
    },
  };
  fixture.visibleComposer.readOnly = true;

  assert.equal(promptInput.findComposer(fixture.documentObject), null);
  assert.equal(promptInput.isComposerTarget(fixture.documentObject, fixture.visibleComposer), false);
});

test('pending voice send inserts into the selected composer and clicks its scoped send button', () => {
  const fixture = createFixture();
  const states = [];
  const controller = promptInput.createPendingSendController({
    document: fixture.documentObject,
    window: {},
    Event: FakeEvent,
    InputEvent: FakeInputEvent,
    getLocation: () => 'https://chatgpt.com/c/current',
    onState: (state) => states.push(state),
  });

  const result = controller.start({ sessionId: 1, text: '音声入力テスト', graceMs: 0 });

  assert.equal(result.ok, true);
  assert.equal(fixture.visibleComposer.innerText, '音声入力テスト');
  assert.equal(fixture.hiddenComposer.value, '');
  assert.equal(fixture.scopedSendButton.clicked, 1);
  assert.equal(fixture.hiddenGlobalSendButton.clicked, 0);
  assert.equal(states.at(-1).phase, 'waiting_response');
});

test('voice send waits for persisted arm acknowledgement before clicking', async () => {
  const fixture = createFixture();
  let resolveArm;
  const arm = new Promise((resolve) => { resolveArm = resolve; });
  const prepared = [];
  const committed = [];
  const controller = promptInput.createPendingSendController({
    document: fixture.documentObject,
    window: {},
    Event: FakeEvent,
    InputEvent: FakeInputEvent,
    getLocation: () => 'https://chatgpt.com/c/current',
    createId: (prefix) => `${prefix}-fixed`,
    prepareSubmission: async (item) => {
      prepared.push(item);
      return arm;
    },
    commitSubmission: async (item) => committed.push(item.submissionId),
  });

  const pending = controller.start({
    sessionId: 'session-1',
    turnId: 'turn-1',
    submissionId: 'submission-1',
    pageInstanceId: 'page-1',
    conversationKey: 'https://chatgpt.com/c/current',
    cancelEpoch: 2,
    assistantBaselineKey: 'assistant-2',
    assistantCountBefore: 2,
    baselineKeys: ['assistant-1', 'assistant-2'],
    text: '送信前ACKテスト',
    graceMs: 0,
  });

  assert.equal(typeof pending.then, 'function');
  assert.equal(fixture.scopedSendButton.clicked, 0);
  assert.equal(prepared[0].submissionId, 'submission-1');
  resolveArm({ ok: true, sendAllowed: true, submission: { phase: 'armed' } });
  const result = await pending;

  assert.equal(result.ok, true);
  assert.equal(fixture.scopedSendButton.clicked, 1);
  assert.deepEqual(committed, ['submission-1']);
});

test('failed arm acknowledgement clears the synthetic transcript and never clicks send', async () => {
  const fixture = createFixture();
  const controller = promptInput.createPendingSendController({
    document: fixture.documentObject,
    window: {},
    Event: FakeEvent,
    InputEvent: FakeInputEvent,
    getLocation: () => 'https://chatgpt.com/c/current',
    prepareSubmission: async () => ({ ok: false, sendAllowed: false, error: 'persistence failed' }),
  });

  const result = await controller.start({ sessionId: 'session-1', text: '消える本文', graceMs: 0 });

  assert.equal(result.ok, false);
  assert.equal(fixture.scopedSendButton.clicked, 0);
  assert.equal(fixture.visibleComposer.innerText, '');
});

test('commit failure invalidates the armed submission after the prompt was clicked', async () => {
  const fixture = createFixture();
  const invalidated = [];
  const controller = promptInput.createPendingSendController({
    document: fixture.documentObject,
    window: {},
    Event: FakeEvent,
    InputEvent: FakeInputEvent,
    getLocation: () => 'https://chatgpt.com/c/current',
    prepareSubmission: async () => ({ ok: true, sendAllowed: true, submission: { phase: 'armed' } }),
    commitSubmission: async () => { throw new Error('commit failed'); },
    invalidateSubmission: async (_item, reason) => invalidated.push(reason),
  });

  const result = await controller.start({ sessionId: 'session-1', text: '送信される本文', graceMs: 0 });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(result.ok, true);
  assert.equal(fixture.scopedSendButton.clicked, 1);
  assert.deepEqual(invalidated, ['submission-commit-failed']);
});
