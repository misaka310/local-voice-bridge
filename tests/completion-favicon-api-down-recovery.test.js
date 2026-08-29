'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const BACKGROUND_PATH = path.resolve(__dirname, '../extension/background.js');

test('completion recovery runs before local API polling so API outages cannot suppress it', () => {
  const source = fs.readFileSync(BACKGROUND_PATH, 'utf8');
  const start = source.indexOf('function scheduleExternalControlPoll');
  const end = source.indexOf('async function initializeBackgroundRuntime', start);
  assert.ok(start >= 0 && end > start, 'external control poll function must exist');

  const block = source.slice(start, end);
  const recoveryIndex = block.indexOf('requestAutoRecheckForRegisteredTabs(');
  const apiPollIndex = block.indexOf('await syncExternalControlPanel()');

  assert.ok(recoveryIndex >= 0, 'recovery sweep must be triggered by the service-worker wake path');
  assert.ok(apiPollIndex >= 0, 'local API poll must remain present');
  assert.ok(recoveryIndex < apiPollIndex, 'recovery sweep must happen before the fallible local API poll');
});
