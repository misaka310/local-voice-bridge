#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const candidates = [
  process.env.PYTHON ? { command: process.env.PYTHON, prefixArgs: [] } : null,
  { command: path.join(ROOT, 'local-api', '.venv', 'Scripts', 'python.exe'), prefixArgs: [] },
  { command: path.join(ROOT, 'local-api', '.venv', 'bin', 'python'), prefixArgs: [] },
  process.platform === 'win32' && process.env.LOCALAPPDATA
    ? { command: path.join(process.env.LOCALAPPDATA, 'Programs', 'Python', 'Python311', 'python.exe'), prefixArgs: [] }
    : null,
  process.platform === 'win32' ? { command: 'py', prefixArgs: ['-3.11'] } : null,
  { command: process.platform === 'win32' ? 'python' : 'python3', prefixArgs: [] },
  { command: 'python', prefixArgs: [] },
].filter(Boolean);

function isLocalPath(command) {
  return path.isAbsolute(command) || command.includes(path.sep);
}

for (const candidate of candidates) {
  const { command, prefixArgs } = candidate;
  if (isLocalPath(command) && (!fs.existsSync(command) || !fs.statSync(command).isFile())) continue;
  const result = spawnSync(command, [...prefixArgs, 'scripts/check-public-tree.py'], {
    cwd: ROOT,
    stdio: 'inherit',
    windowsHide: true,
    shell: false,
  });
  if (!result.error) process.exit(result.status ?? 1);
  if (result.error.code !== 'ENOENT') {
    console.error(result.error.message);
    process.exit(1);
  }
}

console.error('Python was not found. Run setup-voice-env.cmd or install Python 3.11.');
process.exit(1);
