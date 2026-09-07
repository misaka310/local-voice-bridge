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

function collectFiles(relativeDir, allowedExtensions) {
  const absoluteDir = path.join(ROOT, relativeDir);
  const found = [];
  for (const entry of fs.readdirSync(absoluteDir, { withFileTypes: true })) {
    const relativePath = path.posix.join(relativeDir.replaceAll('\\', '/'), entry.name);
    if (entry.isDirectory()) {
      found.push(...collectFiles(relativePath, allowedExtensions));
      continue;
    }
    if (entry.isFile() && allowedExtensions.has(path.extname(entry.name).toLowerCase())) {
      found.push(relativePath);
    }
  }
  return found;
}

function productionBoundaryFiles() {
  const localApi = fs.readdirSync(path.join(ROOT, 'local-api'))
    .filter(name => name.endsWith('.py'))
    .map(name => `local-api/${name}`);
  const extension = fs.readdirSync(path.join(ROOT, 'extension'))
    .filter(name => name.endsWith('.js') || name === 'manifest.json')
    .map(name => `extension/${name}`);
  const setup = collectFiles('scripts/setup', new Set(['.ps1', '.py', '.js', '.cmd', '.bat']));
  const launcher = collectFiles('scripts/launcher', new Set(['.cs', '.ps1', '.js', '.cmd', '.bat']));
  const entryPoints = [
    'setup-voice-env.cmd',
    'run-voice-stack.cmd',
    'scripts/build-launcher.ps1',
    'scripts/install-start-menu-shortcut.ps1',
    'scripts/reload-extension.ps1',
    'scripts/start-local-api.ps1',
    'scripts/stop-local-api.ps1',
    'scripts/uninstall-local-voice-bridge.ps1',
  ];
  return [...new Set([...localApi, ...extension, ...setup, ...launcher, ...entryPoints])];
}

const boundaryFiles = productionBoundaryFiles();
const allowedLoopbackPorts = new Set(['8717']);
const loopbackUrlPattern = /https?:\/\/(?:127\.0\.0\.1|localhost|\[?::1\]?):(\d+)/g;

for (const relativePath of boundaryFiles) {
  const text = read(relativePath);
  for (const match of text.matchAll(loopbackUrlPattern)) {
    const port = match[1];
    if (!allowedLoopbackPorts.has(port)) {
      fail(`${relativePath}: production runtime/setup may not add localhost service port ${port} without an approved architecture change`);
    }
  }
}

if (failures.length) {
  console.error('RUNTIME BOUNDARY CHECK: FAIL');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('RUNTIME BOUNDARY CHECK: PASS');
console.log('- production runtime, setup, and launcher paths are limited to the Local Voice Bridge-owned loopback service');
console.log('- no additional localhost service ports are present in production boundaries');
