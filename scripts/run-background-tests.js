'use strict';

const { spawnSync } = require('node:child_process');

function run(args) {
  const result = spawnSync(process.execPath, args, {
    cwd: process.cwd(),
    encoding: 'utf8',
    maxBuffer: 20 * 1024 * 1024,
    windowsHide: true,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  if (result.status === null) {
    console.error(`background test child exited without a status (signal=${result.signal || 'none'})`);
    return 1;
  }
  if (result.status !== 0) {
    console.error(`background test child failed (status=${result.status}): ${process.execPath} ${args.join(' ')}`);
  }
  return result.status;
}

const major = Number.parseInt(process.versions.node.split('.')[0], 10);
const coreArgs = ['--test'];
if (major >= 20) {
  coreArgs.push('--experimental-test-coverage', '--test-coverage-lines=95');
}
coreArgs.push(
  'tests/background-core.test.js',
  'tests/background-settings-core.test.js',
  'tests/background-runtime-core.test.js',
);

let status = run(coreArgs);
if (status === 0) {
  status = run([
    '--test',
    'tests/background-reference-queue.test.js',
    'tests/background-auto-recheck.test.js',
    'tests/background-state-publisher.test.js',
    'tests/background-control-heartbeat.test.js',
    'tests/background-tab-reconnect.test.js',
    'tests/background-external-panel.test.js',
    'tests/background-message-router.test.js',
    'tests/background-playback-queue.test.js',
    'tests/background-runtime-store.test.js',
    'tests/background-queue-core.test.js',
    'tests/content-text-core.test.js',
    'tests/assistant-text-extractor.test.js',
    'tests/auto-speech-controller.test.js',
    'tests/auto-speech-completion-marker.test.js',
    'tests/content-dom-observer.test.js',
    'tests/content-completion-marker.test.js',
    'tests/content-message-router.test.js',
    'tests/content-mic-keepalive.test.js',
    'tests/e2e-profile-cleanup.test.js',
    'tests/brave-mic-runner.test.js',
    'tests/delivery-id-core.test.js',
    'tests/options-page.test.js',
    'tests/options-settings.test.js',
    'tests/prompt-input-core.test.js',
    'tests/live-browser-core.test.js',
    'tests/live-content-controller.test.js',
    'tests/background-live-client.test.js',
    'tests/background-local-api-client.test.js',
  ]);
}

process.exit(status);
