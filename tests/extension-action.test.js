'use strict';

const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..');

test('extension toolbar click opens browser-specific options instead of doing nothing', () => {
  const background = fs.readFileSync(path.join(ROOT, 'extension', 'background.js'), 'utf8');
  assert.match(background, /chrome\.action\.onClicked\.addListener/);
  assert.match(background, /chrome\.runtime\.openOptionsPage\(\)/);
});
