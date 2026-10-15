'use strict';

const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..');
const ACTION_SCRIPT = path.join(ROOT, 'extension', 'background-action-navigation.js');
const ENTRY_SCRIPT = path.join(ROOT, 'extension', 'background-entry.js');

test('extension toolbar click opens browser-specific options instead of doing nothing', async () => {
  const listeners = [];
  let openCount = 0;
  const context = {
    chrome: {
      action: {
        onClicked: {
          addListener(listener) {
            listeners.push(listener);
          },
        },
      },
      runtime: {
        openOptionsPage() {
          openCount += 1;
          return Promise.resolve();
        },
      },
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(ACTION_SCRIPT, 'utf8'), context, { filename: ACTION_SCRIPT });

  assert.equal(listeners.length, 1);
  await listeners[0]();
  assert.equal(openCount, 1);
});

test('service worker entry registers the focused toolbar navigation module', () => {
  const entry = fs.readFileSync(ENTRY_SCRIPT, 'utf8');
  assert.match(entry, /'background-action-navigation\.js'/);
});
