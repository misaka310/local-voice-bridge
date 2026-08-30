#!/usr/bin/env node
'use strict';

const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const node = process.execPath;
const steps = [
  [node, ['scripts/run-public-check.js']],
  [node, ['scripts/check-architecture.js']],
  [node, ['scripts/check-tray-snapshot-sync.js']],
  [node, ['scripts/run-python-tests.js']],
  [node, ['scripts/run-background-tests.js']],
  [node, ['scripts/run-mock-e2e.js']],
];

for (const [command, args] of steps) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if ((result.status ?? 1) !== 0) {
    console.error(`CI step failed (${result.status ?? 1}): ${command} ${args.join(' ')}`);
    process.exit(result.status ?? 1);
  }
}

console.log('Local Voice Bridge CI: PASS');
