'use strict';

const { spawnSync } = require('node:child_process');

function run(args) {
  const result = spawnSync(process.execPath, args, {
    cwd: process.cwd(),
    stdio: 'inherit',
    windowsHide: true,
  });
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  if (result.status === null) {
    console.error(`background test child exited without a status (signal=${result.signal || 'none'})`);
    return 1;
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
    'tests/background-external-panel.test.js',
    'tests/delivery-id-core.test.js',
    'tests/options-page.test.js',
    'tests/prompt-input-core.test.js',
  ]);
}

process.exit(status);
