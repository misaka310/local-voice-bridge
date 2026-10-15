#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const script = path.join(ROOT, 'scripts', 'build-launcher.ps1');
const output = path.join(ROOT, 'LocalVoiceBridge.exe');
const candidates = [
  process.env.POWERSHELL,
  'powershell.exe',
  'powershell',
  'pwsh',
].filter(Boolean);

function toWindowsPath(value) {
  const match = value.match(/^\/mnt\/([a-zA-Z])\/(.*)$/);
  if (!match) return value;
  return `${match[1].toUpperCase()}:\\${match[2].replaceAll('/', '\\')}`;
}

function usesWindowsPowerShell(command) {
  return /(?:^|[\\/])powershell(?:\.exe)?$/i.test(command);
}

fs.rmSync(output, { force: true });

for (const command of candidates) {
  if (path.isAbsolute(command) && !fs.existsSync(command)) continue;
  const scriptArg = usesWindowsPowerShell(command) ? toWindowsPath(script) : script;
  const result = spawnSync(command, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scriptArg], {
    cwd: ROOT,
    windowsHide: true,
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) {
    if (result.error.code === 'ENOENT') continue;
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) process.exit(result.status ?? 1);
  if (!fs.existsSync(output) || !fs.statSync(output).isFile()) {
    console.error(`PowerShell exited successfully but did not create ${output}`);
    process.exit(1);
  }
  process.exit(0);
}

console.error('Windows PowerShell was not found. Run this build on Windows or set POWERSHELL.');
process.exit(1);
