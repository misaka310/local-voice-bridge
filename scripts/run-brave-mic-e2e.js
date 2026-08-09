#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const playwrightCli = require.resolve('@playwright/test/cli');
const config = path.join('scripts', 'playwright-mock.config.js');
const profile = path.resolve(ROOT, '..', '_runtime', 'localvoice-brave-test-profile');
const brave = process.env.LOCAL_VOICE_BROWSER_EXECUTABLE
  || path.join(process.env.PROGRAMFILES || 'C:\\Program Files', 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe');

if (!fs.existsSync(brave)) {
  console.error(`Brave executable not found: ${brave}`);
  process.exit(1);
}

const env = {
  ...process.env,
  LOCAL_VOICE_BROWSER_EXECUTABLE: brave,
  LOCAL_VOICE_TEST_PROFILE: profile,
  PLAYWRIGHT_HEADED: process.env.LOCAL_VOICE_E2E_HEADED === '1' ? '1' : '0',
};

console.log(`[brave-mic-e2e] executable=${brave}`);
console.log(`[brave-mic-e2e] isolated-profile=${profile}`);

const runner = spawn(process.execPath, [
  playwrightCli,
  'test',
  '--config',
  config,
  'extension-mock-ci.spec.js',
  '--grep',
  'microphone transcript commits and sends through a ProseMirror composer',
  '--workers=1',
], {
  cwd: ROOT,
  env,
  stdio: 'inherit',
  shell: false,
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => runner.kill(signal));
}

runner.on('error', (error) => {
  console.error(error.message);
  process.exit(1);
});
runner.on('exit', (code) => process.exit(code ?? 1));
