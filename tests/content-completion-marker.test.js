'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = path.resolve(__dirname, '../extension/content-completion-marker.js');

class Element {
  constructor(tag) {
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
  constructor() {
    this.title = 'ChatGPT';
    this.head = new Element('head');
  }
  createElement(tag) { return new Element(tag); }
  getElementById(id) { return this.head.querySelectorAll('*').find((element) => element.id === id) || null; }
  querySelectorAll(selector) { return this.head.querySelectorAll(selector); }
  hasFocus() { return false; }
}

class MutationObserver {
  observe() {}
  disconnect() {}
}

function createMarker() {
  const document = new Document();
  const context = vm.createContext({ console, encodeURIComponent });
  context.globalThis = context;
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, { filename: SOURCE });
  const marker = context.LocalVoiceCompletionMarker.create({
    document,
    window: { setTimeout },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    MutationObserver,
    runtimeMessage: async () => ({ active: false }),
  });
  return { document, marker };
}

function activeIcons(document) {
  return document.querySelectorAll('link').filter((link) => /(^|\s)icon(\s|$)/i.test(link.rel));
}

test('completion uses only the yellow favicon and leaves the tab title unchanged', async () => {
  const { document, marker } = createMarker();
  const original = document.createElement('link');
  original.rel = 'shortcut icon';
  original.href = 'https://chatgpt.com/favicon.ico';
  document.head.appendChild(original);

  await marker.initialize();
  marker.markResponseCompleted();

  const yellow = document.getElementById('local-voice-completion-favicon');
  assert.equal(document.title, 'ChatGPT');
  assert.ok(yellow);
  assert.deepEqual(activeIcons(document), [yellow]);
  assert.equal(original.hasAttribute('rel'), false);

  marker.clear();

  assert.equal(document.title, 'ChatGPT');
  assert.equal(document.getElementById('local-voice-completion-favicon'), null);
  assert.equal(original.rel, 'shortcut icon');
});

test('ChatGPT cannot restore its own favicon while completion remains pending', async () => {
  const { document, marker } = createMarker();
  await marker.initialize();
  marker.markResponseCompleted();

  const rewritten = document.createElement('link');
  rewritten.rel = 'icon';
  rewritten.href = 'https://chatgpt.com/new-favicon.ico';
  document.head.appendChild(rewritten);
  marker.sync();

  const yellow = document.getElementById('local-voice-completion-favicon');
  assert.deepEqual(activeIcons(document), [yellow]);
  assert.equal(rewritten.hasAttribute('rel'), false);

  marker.clear();
  assert.equal(rewritten.rel, 'icon');
});
