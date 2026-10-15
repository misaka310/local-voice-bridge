#!/usr/bin/env node
'use strict';

const path = require('path');
const { spawn } = require('child_process');
const { cleanupStaleE2EProfiles } = require('./e2e-profile-cleanup');

const ROOT = path.resolve(__dirname, '..');
const playwrightCli = require.resolve('@playwright/test/cli');

function cleanupProfiles() {
  const result = cleanupStaleE2EProfiles(ROOT);
  if (result.removed) {
    console.log(`[e2e] removed ${result.removed} stale browser profile(s)`);
  }
  if (result.failed) {
    console.warn(`[e2e] could not inspect or remove ${result.failed} browser profile(s)`);
  }
}

cleanupProfiles();
const config = path.join('scripts', 'playwright-mock.config.js');
const runner = spawn(process.execPath, [
  playwrightCli,
  'test',
  '--config',
  config,
], {
  cwd: ROOT,
  stdio: 'inherit',
  shell: false,
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => runner.kill(signal));
}

runner.on('error', (error) => {
  console.error(error.message);
  cleanupProfiles();
  process.exit(1);
});
runner.on('exit', (code) => {
  cleanupProfiles();
  process.exit(code ?? 1);
});
