'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const { isRelevantMutationBatch, isResponseError } = require('../extension/content-mutation-filter.js');

function element(options = {}) {
  const {
    assistant = false,
    assistantAncestor = false,
    generationControl = false,
    completionControl = false,
    errorControl = false,
    articleAssistant = false,
  } = options;
  return {
    nodeType: 1,
    tagName: articleAssistant ? 'ARTICLE' : 'DIV',
    textContent: articleAssistant ? 'ChatGPT response' : '',
    getAttribute(name) {
      return name === 'aria-label' && articleAssistant ? 'assistant' : null;
    },
    matches(selector) {
      if (selector === '[data-message-author-role="assistant"]') return assistant;
      if (selector === 'article') return articleAssistant;
      if (selector.includes('stop-button')) return generationControl;
      if (selector.includes('copy-turn-action-button')) return completionControl;
      if (selector.includes('response-error')) return errorControl;
      return false;
    },
    closest(selector) {
      if (selector === '[data-message-author-role="assistant"]' && assistantAncestor) return {};
      if (selector === 'article' && articleAssistant) return this;
      if (selector.includes('stop-button') && generationControl) return this;
      if (selector.includes('copy-turn-action-button') && completionControl) return this;
      if (selector.includes('response-error') && errorControl) return this;
      return null;
    },
    querySelector() { return null; },
  };
}

function mutation(target, { addedNodes = [], removedNodes = [] } = {}) {
  return { target, addedNodes, removedNodes };
}

function loadDomObserverApi() {
  const source = fs.readFileSync(path.resolve(__dirname, '../extension/content-dom-observer.js'), 'utf8');
  const sandbox = {
    globalThis: {
      LocalVoiceContentMutationFilter: require('../extension/content-mutation-filter.js'),
      ContentTextCore: {},
      LocalVoiceAssistantText: { getAssistantNodes: () => [] },
      LocalVoiceAutoSpeech: {
        createAutoSpeechController: () => ({
          markExistingMessagesAsSeen() {},
          rebaseline() {},
          reportLatestSnapshot() { return false; },
          inspectLatestAssistant() { return false; },
          scheduleInspect() { return false; },
        }),
      },
    },
  };
  vm.runInNewContext(source, sandbox, { filename: 'content-dom-observer.js' });
  return sandbox.globalThis.LocalVoiceContentDomObserver;
}

test('blank new conversation status is derived from route and message DOM without polling', () => {
  const statusChanges = [];
  const location = { pathname: '/' };
  let hasMessage = false;
  const document = {
    querySelector(selector) {
      if (selector.includes('data-message-author-role')) return hasMessage ? {} : null;
      return null;
    },
  };
  const controller = loadDomObserverApi().create({
    document,
    location,
    getSettings: () => ({}),
    isEnabled: () => false,
    setNewConversation: (value) => statusChanges.push(value),
  });
  const unrelated = mutation(element());

  assert.equal(controller.scheduleInspect([unrelated]), false);
  assert.deepEqual(statusChanges, [true]);
  assert.equal(controller.scheduleInspect([unrelated]), false);
  assert.deepEqual(statusChanges, [true]);

  hasMessage = true;
  const userMessage = element();
  userMessage.matches = (selector) => selector === '[data-message-author-role]';
  assert.equal(controller.scheduleInspect([mutation(element(), { addedNodes: [userMessage] })]), false);
  assert.deepEqual(statusChanges, [true, false]);

  hasMessage = false;
  location.pathname = '/c/example';
  assert.equal(controller.scheduleInspect([unrelated]), false);
  assert.deepEqual(statusChanges, [true, false]);
});

test('unrelated body churn is rejected before assistant scanning across 30 tabs', () => {
  for (let tab = 0; tab < 30; tab += 1) {
    assert.equal(isRelevantMutationBatch([mutation(element())]), false);
  }
});

test('assistant subtree and response controls remain relevant', () => {
  assert.equal(isRelevantMutationBatch([mutation(element({ assistantAncestor: true }))]), true);
  assert.equal(isRelevantMutationBatch([
    mutation(element(), { addedNodes: [element({ generationControl: true })] }),
  ]), true);
  assert.equal(isRelevantMutationBatch([
    mutation(element(), { addedNodes: [element({ completionControl: true })] }),
  ]), true);
  assert.equal(isRelevantMutationBatch([
    mutation(element(), { addedNodes: [element({ errorControl: true })] }),
  ]), true);
  assert.equal(isRelevantMutationBatch([]), true);
});

test('ordinary completed prose mentioning an error is not treated as a ChatGPT failure', () => {
  const turn = {
    querySelector(selector) {
      if (selector.includes('regenerate-response-button')) return {};
      if (selector.includes('copy-turn-action-button')) return {};
      return null;
    },
  };
  const assistant = {
    textContent: 'Network error means the connection failed, but this is ordinary explanatory prose.',
    closest: () => turn,
  };
  const document = { querySelector: () => null, querySelectorAll: () => [] };

  assert.equal(isResponseError(document, [assistant]), false);
});

test('ChatGPT text generation error marks the tab without invoking Auto speech', () => {
  const marks = [];
  const errorNode = element({ errorControl: true });
  errorNode.textContent = 'Something went wrong while generating the response.';
  const document = {
    querySelector(selector) {
      if (selector.includes('stop-button')) return null;
      if (selector.includes('response-error')) return errorNode;
      if (selector.includes('data-message-author-role')) return {};
      return null;
    },
    querySelectorAll() { return []; },
  };
  const controller = loadDomObserverApi().create({
    document,
    location: { pathname: '/c/example' },
    getSettings: () => ({}),
    isEnabled: () => true,
    setNewConversation() {},
    markResponseGenerating: () => marks.push('generating'),
    markResponseGenerationEnded() {},
    markResponseError: () => marks.push('error'),
  });

  assert.equal(controller.scheduleInspect([mutation(errorNode)]), false);
  assert.deepEqual(marks, ['error']);
});

test('generating favicon is cleared when the generation control disappears', () => {
  const marks = [];
  const generationNode = element({ generationControl: true });
  let generating = true;
  const document = {
    querySelector(selector) {
      if (selector.includes('stop-button')) return generating ? generationNode : null;
      if (selector.includes('data-message-author-role')) return {};
      return null;
    },
    querySelectorAll() { return []; },
  };
  const controller = loadDomObserverApi().create({
    document,
    location: { pathname: '/c/example' },
    getSettings: () => ({}),
    isEnabled: () => true,
    setNewConversation() {},
    markResponseGenerating: () => marks.push('generating'),
    markResponseGenerationEnded: () => marks.push('generation-ended'),
    markResponseError: () => marks.push('error'),
  });

  controller.scheduleInspect([mutation(element(), { addedNodes: [generationNode] })]);
  generating = false;
  controller.scheduleInspect([mutation(element(), { removedNodes: [generationNode] })]);

  assert.deepEqual(marks, ['generating', 'generation-ended']);
});
