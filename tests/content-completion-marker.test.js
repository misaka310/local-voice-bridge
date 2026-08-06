'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const SOURCE = path.join(ROOT, 'extension/content-completion-marker.js');
const DOM_OBSERVER_SOURCE = path.join(ROOT, 'extension/content-dom-observer.js');
const AGENTS = path.join(ROOT, 'AGENTS.md');
const TAB_STATUS_DESIGN = path.join(ROOT, 'docs/tab-status-and-resource-design.md');

class Element {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag).toUpperCase();
    this.parentNode = null;
    this.children = [];
    this.attributes = new Map();
  }
  get id() { return this.getAttribute('id') || ''; }
  set id(value) { this.setAttribute('id', value); }
  get rel() { return this.getAttribute('rel') || ''; }
  set rel(value) { this.setAttribute('rel', value); }
  get type() { return this.getAttribute('type') || ''; }
  set type(value) { this.setAttribute('type', value); }
  get href() { return this.getAttribute('href') || ''; }
  set href(value) { this.setAttribute('href', value); }
  get lastElementChild() { return this.children.at(-1) || null; }
  setAttribute(name, value) { this.attributes.set(String(name), String(value)); }
  getAttribute(name) { return this.attributes.has(String(name)) ? this.attributes.get(String(name)) : null; }
  hasAttribute(name) { return this.attributes.has(String(name)); }
  removeAttribute(name) { this.attributes.delete(String(name)); }
  appendChild(child) {
    child.remove();
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  querySelectorAll(selector) {
    const all = this.children.flatMap((child) => [child, ...child.querySelectorAll('*')]);
    if (selector === '*') return all;
    if (selector === 'link') return all.filter((element) => element.tagName === 'LINK');
    const match = selector.match(/^link\[([^\]]+)\]$/);
    if (match) return all.filter((element) => element.tagName === 'LINK' && element.hasAttribute(match[1]));
    throw new Error(`Unsupported selector: ${selector}`);
  }
}

class Document {
  constructor(focused = false) {
    this.title = 'ChatGPT';
    this.head = new Element('head');
    this.focused = focused;
  }
  createElement(tag) { return new Element(tag); }
  getElementById(id) { return this.head.querySelectorAll('*').find((element) => element.id === id) || null; }
  querySelectorAll(selector) { return this.head.querySelectorAll(selector); }
  hasFocus() { return this.focused; }
}

class MutationObserver {
  observe() {}
  disconnect() {}
}

function createSessionStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(String(key), String(value)); },
    removeItem(key) { values.delete(String(key)); },
    snapshot() { return Object.fromEntries(values); },
  };
}

function createMarker({ active = false, session = {} } = {}) {
  const document = new Document(active);
  const sessionStorage = createSessionStorage(session);
  const context = vm.createContext({ console, encodeURIComponent, Object });
  context.globalThis = context;
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, { filename: SOURCE });
  const markerApi = context.LocalVoiceCompletionMarker;
  const marker = markerApi.create({
    document,
    window: { setTimeout },
    sessionStorage,
    MutationObserver,
    runtimeMessage: async () => ({ active }),
  });
  return { document, marker, markerApi, sessionStorage };
}

function activeIcons(document) {
  return document.querySelectorAll('link').filter((link) => /(^|\s)icon(\s|$)/i.test(link.rel));
}

function statusIcon(document) {
  return document.getElementById('local-voice-completion-favicon');
}

function decodedSvg(icon) {
  return decodeURIComponent(String(icon.href).split(',').slice(1).join(','));
}

test('generating, complete, playing, and text-response error use distinct static favicons', async () => {
  const { document, marker } = createMarker();
  const original = document.createElement('link');
  original.rel = 'shortcut icon';
  original.href = 'https://chatgpt.com/favicon.ico';
  document.head.appendChild(original);
  await marker.initialize();

  marker.markResponseGenerating();
  let icon = statusIcon(document);
  assert.equal(marker.displayedStatus(), 'generating');
  assert.equal(icon.getAttribute('data-local-voice-status'), 'generating');
  assert.match(decodedSvg(icon), /#2563eb/);
  assert.match(decodedSvg(icon), /<circle/);

  marker.markResponseCompleted();
  icon = statusIcon(document);
  assert.equal(marker.displayedStatus(), 'complete');
  assert.equal(icon.getAttribute('data-local-voice-status'), 'complete');
  assert.match(decodedSvg(icon), /#facc15/);

  marker.markPlaybackStarted();
  icon = statusIcon(document);
  assert.equal(marker.displayedStatus(), 'playing');
  assert.equal(icon.getAttribute('data-local-voice-status'), 'playing');
  assert.match(decodedSvg(icon), /#7c3aed/);

  marker.markPlaybackCompleted();
  marker.markResponseError();
  icon = statusIcon(document);
  assert.equal(marker.displayedStatus(), 'error');
  assert.equal(icon.getAttribute('data-local-voice-status'), 'error');
  assert.match(decodedSvg(icon), /#dc2626/);
  assert.deepEqual(activeIcons(document), [icon]);
  assert.equal(original.hasAttribute('rel'), false);
});

test('playback temporarily overrides completion and returns to yellow until viewed', async () => {
  const { document, marker } = createMarker();
  await marker.initialize();
  marker.markResponseCompleted();
  marker.markPlaybackStarted();
  assert.equal(marker.displayedStatus(), 'playing');

  marker.markPlaybackCompleted();
  assert.equal(marker.displayedStatus(), 'complete');
  assert.equal(statusIcon(document).getAttribute('data-local-voice-status'), 'complete');

  marker.clear();
  assert.equal(marker.displayedStatus(), 'idle');
  assert.equal(statusIcon(document), null);
});

test('viewing during playback acknowledges completion without hiding the speaker', async () => {
  const { document, marker } = createMarker();
  await marker.initialize();
  marker.markResponseCompleted();
  marker.markPlaybackStarted();

  marker.clear();
  assert.equal(marker.displayedStatus(), 'playing');
  assert.equal(statusIcon(document).getAttribute('data-local-voice-status'), 'playing');

  marker.markPlaybackCompleted();
  assert.equal(marker.displayedStatus(), 'idle');
  assert.equal(statusIcon(document), null);
});

test('text-response error persists until the tab is acknowledged', async () => {
  const { document, marker, sessionStorage } = createMarker();
  await marker.initialize();
  marker.markResponseError();

  assert.equal(marker.displayedStatus(), 'error');
  assert.equal(sessionStorage.snapshot().localVoiceTerminalStatus, 'error');
  assert.equal(statusIcon(document).getAttribute('data-local-voice-status'), 'error');

  marker.clear();
  assert.equal(marker.displayedStatus(), 'idle');
  assert.equal(sessionStorage.snapshot().localVoiceTerminalStatus, undefined);
});

test('audio failure and stop clear only the speaker and never create a red favicon', async () => {
  const { document, marker, sessionStorage } = createMarker();
  await marker.initialize();
  marker.markResponseCompleted();
  marker.markPlaybackStarted();
  marker.markPlaybackError();

  assert.equal(marker.displayedStatus(), 'complete');
  assert.equal(statusIcon(document).getAttribute('data-local-voice-status'), 'complete');
  assert.equal(sessionStorage.snapshot().localVoiceTerminalStatus, 'complete');

  marker.markPlaybackStarted();
  marker.markPlaybackStopped();
  assert.equal(marker.displayedStatus(), 'complete');
  assert.equal(statusIcon(document).getAttribute('data-local-voice-status'), 'complete');
});

test('legacy pending completion is migrated without changing the title', async () => {
  const { document, marker, sessionStorage } = createMarker({
    session: { localVoiceCompletionPending: '1' },
  });
  document.title = '● ChatGPT';
  await marker.initialize();

  assert.equal(document.title, 'ChatGPT');
  assert.equal(marker.displayedStatus(), 'complete');
  assert.equal(sessionStorage.snapshot().localVoiceCompletionPending, undefined);
  assert.equal(sessionStorage.snapshot().localVoiceTerminalStatus, 'complete');
});

test('ChatGPT cannot restore its own favicon while a status remains active', async () => {
  const { document, marker } = createMarker();
  await marker.initialize();
  marker.markResponseGenerating();

  const rewritten = document.createElement('link');
  rewritten.rel = 'icon';
  rewritten.href = 'https://chatgpt.com/new-favicon.ico';
  document.head.appendChild(rewritten);
  marker.sync();

  const icon = statusIcon(document);
  assert.deepEqual(activeIcons(document), [icon]);
  assert.equal(rewritten.hasAttribute('rel'), false);

  marker.markResponseCompleted();
  marker.clear();
  assert.equal(rewritten.rel, 'icon');
});

test('head mutation filter ignores unrelated churn and reacts to favicon changes', () => {
  const { document, markerApi } = createMarker();
  const unrelated = document.createElement('style');
  const icon = document.createElement('link');
  icon.rel = 'icon';

  assert.equal(markerApi.headMutationNeedsSync([{
    type: 'characterData',
    target: unrelated,
  }]), false);
  assert.equal(markerApi.headMutationNeedsSync([{
    type: 'childList',
    target: document.head,
    addedNodes: [unrelated],
    removedNodes: [],
  }]), false);
  assert.equal(markerApi.headMutationNeedsSync([{
    type: 'childList',
    target: document.head,
    addedNodes: [icon],
    removedNodes: [],
  }]), true);
  assert.equal(markerApi.headMutationNeedsSync([{
    type: 'attributes',
    target: icon,
  }]), true);
});

test('blank new conversation uses a static plus and yields to active states', async () => {
  const { document, marker, sessionStorage } = createMarker();
  await marker.initialize();

  marker.setNewConversation(true);
  let icon = statusIcon(document);
  assert.equal(marker.displayedStatus(), 'new');
  assert.equal(icon.getAttribute('data-local-voice-status'), 'new');
  assert.match(decodedSvg(icon), /#0891b2/);
  assert.match(decodedSvg(icon), /M16 7v18M7 16h18/);
  assert.equal(sessionStorage.snapshot().localVoiceTerminalStatus, undefined);

  marker.markResponseGenerating();
  assert.equal(marker.displayedStatus(), 'generating');

  marker.markResponseCompleted();
  assert.equal(marker.displayedStatus(), 'complete');
  marker.clear();
  assert.equal(marker.displayedStatus(), 'new');

  marker.setNewConversation(false);
  assert.equal(marker.displayedStatus(), 'idle');
  assert.equal(statusIcon(document), null);
});

test('blank conversation detection is event driven and does not poll each tab', () => {
  const content = fs.readFileSync(DOM_OBSERVER_SOURCE, 'utf8');

  assert.match(content, /function isBlankNewConversation\(\)/);
  assert.match(content, /location\.pathname !== '\/'/);
  assert.match(content, /data-message-author-role/);
  assert.match(content, /ctx\.setNewConversation\(next\)/);
  assert.match(content, /function newConversationMutationNeedsRefresh\(/);
  assert.match(content, /addedNodes/);
  assert.match(content, /scheduleInspect/);
  assert.doesNotMatch(content, /setInterval\(/);
  assert.doesNotMatch(content, /addEventListener\(['"]scroll/);
});

test('17 is the sole owner of ChatGPT tab status and favicon behavior', () => {
  const agents = fs.readFileSync(AGENTS, 'utf8');
  const design = fs.readFileSync(TAB_STATUS_DESIGN, 'utf8');

  assert.match(agents, /タブ状態とfaviconの所有権/);
  assert.match(agents, /唯一の所有者/);
  assert.match(agents, /73_chatgpt-tab-memo.*作業メモだけ/);
  assert.match(design, /faviconの状態/);
  assert.match(design, /空の新規会話/);
  assert.match(design, /回答完了・未確認/);
  assert.match(design, /読み上げ処理中/);
});
