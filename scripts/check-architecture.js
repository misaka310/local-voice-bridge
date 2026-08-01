#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const failures = [];

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8');
}

function requireFile(relativePath) {
  if (!fs.existsSync(path.join(ROOT, relativePath))) {
    failures.push(`${relativePath}: required architecture module is missing`);
  }
}

function requireText(relativePath, expected, reason) {
  const text = read(relativePath);
  if (!text.includes(expected)) {
    failures.push(`${relativePath}: ${reason}`);
  }
}

function forbidText(relativePath, forbidden, reason) {
  const text = read(relativePath);
  if (text.includes(forbidden)) {
    failures.push(`${relativePath}: ${reason}`);
  }
}

function capLines(relativePath, maximum) {
  const count = read(relativePath).split(/\r?\n/).length;
  if (count > maximum) {
    failures.push(
      `${relativePath}: ${count} lines exceeds architecture cap ${maximum}; extract a focused module instead of growing the orchestrator`,
    );
  }
}

for (const file of [
  'local-api/state_normalization.py',
  'local-api/browser_runtime_state.py',
  'local-api/durable_outbox.py',
  'local-api/http_io.py',
  'local-api/runtime_readiness.py',
  'local-api/voice_runtime.py',
  'extension/background-core.js',
  'extension/background-settings-core.js',
  'extension/background-runtime-core.js',
  'extension/background-control-sync.js',
]) {
  requireFile(file);
}

capLines('local-api/control_state.py', 350);
capLines('local-api/server.py', 810);
capLines('extension/background.js', 1125);
capLines('extension/background-settings-core.js', 200);
capLines('extension/background-runtime-core.js', 190);
capLines('extension/background-control-sync.js', 330);

requireText(
  'CONTRIBUTING.md',
  '## Architecture boundaries',
  'contributors must receive the public architecture and verification contract',
);
requireText(
  'local-api/control_state.py',
  'self._outbox = DurableOutbox()',
  'ControlStateStore must coordinate the durable outbox instead of reimplementing ACK state',
);
requireText(
  'local-api/server.py',
  'from http_io import ResponseWriteError, is_normal_client_disconnect, json_response, request_json',
  'HTTP request/response handling must stay in http_io.py',
);
requireText(
  'local-api/server.py',
  'from runtime_readiness import enrich_snapshot, runtime_snapshot, structured_readiness',
  'readiness composition must stay in runtime_readiness.py',
);
requireText(
  'extension/background-entry.js',
  "'background-settings-core.js'",
  'the service worker must load tested settings rules before background.js',
);
requireText(
  'extension/background-entry.js',
  "'background-runtime-core.js'",
  'the service worker must load pure runtime-state helpers before background.js',
);
requireText(
  'extension/background-entry.js',
  "'background-control-sync.js'",
  'the service worker must load durable control synchronization before background.js',
);
requireText(
  'extension/background.js',
  'BackgroundRuntimeCore.mergeSnapshot',
  'browser runtime restoration must use the tested pure runtime-state module',
);
requireText(
  'extension/background.js',
  'BackgroundControlSync.create',
  'external poll, ACK, and transcript routing must use background-control-sync.js',
);

forbidText(
  'local-api/control_state.py',
  '_SAFE_CONSUMER_ID =',
  'consumer/ACK normalization belongs in durable_outbox.py',
);
forbidText(
  'local-api/control_state.py',
  'def _normalize_browser_runtime',
  'browser runtime schemas belong in browser_runtime_state.py',
);
forbidText(
  'local-api/server.py',
  '_CLIENT_DISCONNECT_ERRNOS =',
  'socket write handling belongs in http_io.py',
);
forbidText(
  'local-api/server.py',
  'def structured_readiness(',
  'readiness composition belongs in runtime_readiness.py',
);
forbidText(
  'extension/background.js',
  'function queueIdentity(',
  'queue identity and merge rules belong in background-runtime-core.js',
);
forbidText(
  'extension/background.js',
  'function sanitizeSettings(',
  'settings migration and normalization belong in background-settings-core.js',
);
forbidText(
  'extension/background.js',
  'let lastExternalCommandId',
  'external delivery cursors belong in background-control-sync.js',
);
forbidText(
  'extension/background.js',
  'async function getBridgeConsumerId(',
  'stable consumer identity belongs in background-control-sync.js',
);
forbidText(
  'extension/background.js',
  'async function applyExternalSettings(',
  'external settings synchronization belongs in background-control-sync.js',
);

if (failures.length) {
  console.error('ARCHITECTURE CHECK: FAIL');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('ARCHITECTURE CHECK: PASS');
console.log('- durable state/outbox/browser schemas are separated');
console.log('- HTTP I/O and voice runtime remain focused modules');
console.log('- orchestrator growth caps are enforced');
