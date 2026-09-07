#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const failures = [];

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8').replace(/\r\n/g, '\n');
}

function fail(message) {
  failures.push(message);
}

function forbidFile(relativePath, reason) {
  if (fs.existsSync(path.join(ROOT, relativePath))) {
    fail(`${relativePath}: ${reason}`);
  }
}

function forbidText(relativePath, forbidden, reason) {
  if (read(relativePath).includes(forbidden)) {
    fail(`${relativePath}: ${reason}`);
  }
}

function runtimeFiles() {
  const localApi = fs.readdirSync(path.join(ROOT, 'local-api'))
    .filter(name => name.endsWith('.py'))
    .map(name => `local-api/${name}`);
  const extension = fs.readdirSync(path.join(ROOT, 'extension'))
    .filter(name => name.endsWith('.js') || name === 'manifest.json')
    .map(name => `extension/${name}`);
  return [...localApi, ...extension];
}

forbidFile(
  'local-api/dictation_pause_notifier.py',
  'Local Voice Bridge must not contain the removed YouTube runtime bridge',
);

for (const marker of [
  'YouTubePauseNotifier',
  'DictationPauseNotifier',
  'YOUTUBE_DICTATION_PAUSE_STATE_URL',
  '127.0.0.1:17654',
  'youtube-dictation-pause-control',
]) {
  for (const relativePath of runtimeFiles()) {
    forbidText(
      relativePath,
      marker,
      `runtime responsibility boundary forbids cross-repo YouTube marker ${marker}`,
    );
  }
}

for (const relativePath of [
  'README.md',
  'ARCHITECTURE.md',
  'docs/operation.md',
  'docs/troubleshooting.md',
]) {
  for (const marker of ['YouTube Dictation Pause Control', 'youtube-dictation-pause-control', '127.0.0.1:17654']) {
    forbidText(
      relativePath,
      marker,
      `current product documentation must not advertise the removed cross-repository integration (${marker})`,
    );
  }
}

const allowedLoopbackPorts = new Set(['8717']);
const loopbackUrlPattern = /https?:\/\/(?:127\.0\.0\.1|localhost|\[?::1\]?):(\d+)/g;
for (const relativePath of runtimeFiles()) {
  const text = read(relativePath);
  for (const match of text.matchAll(loopbackUrlPattern)) {
    const port = match[1];
    if (!allowedLoopbackPorts.has(port)) {
      fail(`${relativePath}: normal runtime may not add localhost service port ${port} without an approved architecture change`);
    }
  }
}

if (failures.length) {
  console.error('RUNTIME BOUNDARY CHECK: FAIL');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('RUNTIME BOUNDARY CHECK: PASS');
console.log('- normal runtime is limited to the Local Voice Bridge-owned loopback service');
console.log('- removed YouTube cross-repository coupling is absent');
