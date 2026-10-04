'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../scripts/run-brave-mic-e2e.js'),
  'utf8',
);

test('Brave microphone E2E is headless unless a visual run is explicitly requested', () => {
  assert.match(
    SOURCE,
    /PLAYWRIGHT_HEADED:\s*process\.env\.LOCAL_VOICE_E2E_HEADED\s*===\s*'1'\s*\?\s*'1'\s*:\s*'0'/,
  );
  assert.doesNotMatch(SOURCE, /PLAYWRIGHT_HEADED:\s*'1'/);
});

test('Brave executable lookup uses configuration instead of a fixed machine path', () => {
  assert.match(SOURCE, /process\.env\.LOCAL_VOICE_BROWSER_EXECUTABLE/);
  assert.match(SOURCE, /process\.env\.PROGRAMFILES/);
  assert.doesNotMatch(SOURCE, /\|\|\s*['"][A-Za-z]:/);
});
