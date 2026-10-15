const { spawnSync } = require('node:child_process');

const major = Number.parseInt(process.versions.node.split('.')[0], 10);
const args = ['--test'];

// Node 20+ supports the built-in test coverage flags used by this repository.
// Node 18 is still supported for contributors, so run the same tests without
// coverage enforcement instead of failing before any test executes.
if (major >= 20) {
  args.push('--experimental-test-coverage', '--test-coverage-lines=95');
}

args.push('tests/background-core.test.js');

const result = spawnSync(process.execPath, args, {
  cwd: process.cwd(),
  stdio: 'inherit',
  windowsHide: true,
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}

process.exit(result.status ?? 1);
