'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-tab-update-watcher.js'),
  'utf8',
);

function loadModule() {
  const context = vm.createContext({ console, setTimeout, clearTimeout });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-tab-update-watcher.js' });
  return context.BackgroundTabUpdateWatcher;
}

function createDocument(state) {
  const form = { querySelector: () => state.pendingAttachment ? {} : null };
  const composer = {
    tagName: 'TEXTAREA',
    get value() { return state.draft; },
    closest(selector) { return selector === 'form' ? form : null; },
  };
  const turn = {
    closest() { return this; },
    querySelector() { return state.hasCompletionControl ? {} : null; },
  };
  const assistant = { closest() { return turn; } };
  return {
    readyState: 'complete',
    visibilityState: 'hidden',
    body: {},
    documentElement: {},
    querySelector(selector) {
      if (selector.includes('stop-button')) return state.generating ? {} : null;
      if (selector.includes('response-error') || selector.includes('conversation-turn-error')) {
        return state.error ? {} : null;
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector === '#prompt-textarea') return [composer];
      if (selector === '[data-message-author-role="assistant"],[data-conversation-role="assistant"]') return [assistant];
      if (selector === 'input[type="file"]') return state.selectedFiles ? [{ files: [1] }] : [];
      return [];
    },
    addEventListener() {},
    removeEventListener() {},
  };
}

function makeWatcher(state, messages = []) {
  const values = new Map();
  class MutationObserverMock {
    observe() {}
    disconnect() {}
  }
  const document = createDocument(state);
  const watcher = loadModule().create({
    document,
    window: { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} },
    sessionStorage: {
      getItem(key) { return values.get(key) || null; },
      setItem(key, value) { values.set(key, String(value)); },
      removeItem(key) { values.delete(key); },
    },
    runtime: { onMessage: { addListener() {}, removeListener() {} } },
    contentScriptVersion: '0.4.6',
    MutationObserver: MutationObserverMock,
    sendMessage(message, callback) {
      messages.push(message);
      callback({ ok: true, reloading: true });
    },
  });
  return { watcher, document, values };
}

test('update safety rejects generating replies, drafts, and selected files', () => {
  const state = {
    generating: false,
    draft: '',
    selectedFiles: false,
    pendingAttachment: false,
    hasCompletionControl: true,
    error: false,
  };
  const { watcher } = makeWatcher(state);
  assert.deepEqual(JSON.parse(JSON.stringify(watcher.inspect())), { safe: true, reason: 'idle' });

  state.generating = true;
  assert.equal(watcher.inspect().safe, false);
  state.generating = false;
  state.draft = '下書き';
  assert.equal(watcher.inspect().safe, false);
  state.draft = '';
  state.selectedFiles = true;
  assert.equal(watcher.inspect().safe, false);
  state.selectedFiles = false;
  state.pendingAttachment = true;
  assert.equal(watcher.inspect().safe, false);
});

test('a reply already generating when an update arrives is saved as yellow-ready before refresh', () => {
  const state = {
    generating: true,
    draft: '',
    selectedFiles: false,
    pendingAttachment: false,
    hasCompletionControl: true,
    error: false,
  };
  const messages = [];
  const { watcher, values } = makeWatcher(state, messages);
  watcher.start();

  state.generating = false;
  assert.equal(watcher.check(), true);
  assert.equal(values.get('localVoiceTerminalStatus'), 'complete');
  assert.equal(values.has('localVoiceCompletionPending'), false);
  assert.deepEqual(JSON.parse(JSON.stringify(messages)), [{
    type: 'bridge-page-ready-for-update',
    contentScriptVersion: '0.4.6',
  }]);
  watcher.dispose();
});

test('an update watcher waits for a completion control and leaves tab focus safety to the background', () => {
  const state = {
    generating: true,
    draft: '',
    selectedFiles: false,
    pendingAttachment: false,
    hasCompletionControl: false,
    error: false,
  };
  const messages = [];
  const { watcher, document } = makeWatcher(state, messages);
  watcher.start();
  state.generating = false;
  assert.equal(watcher.check(), false);
  assert.equal(messages.length, 0);
  document.visibilityState = 'visible';
  state.hasCompletionControl = true;
  assert.equal(watcher.check(), true);
  assert.equal(messages.length, 1);
  watcher.dispose();
});

test('a failed background reply preserves the error marker before a safe update', () => {
  const state = {
    generating: true,
    draft: '',
    selectedFiles: false,
    pendingAttachment: false,
    hasCompletionControl: false,
    error: true,
  };
  const messages = [];
  const { watcher, values } = makeWatcher(state, messages);
  watcher.start();
  state.generating = false;

  assert.equal(watcher.check(), true);
  assert.equal(values.get('localVoiceTerminalStatus'), 'error');
  assert.equal(messages.length, 1);
  watcher.dispose();
});
