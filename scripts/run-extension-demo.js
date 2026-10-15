#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('@playwright/test');
const { fixtureHtml } = require('./demo-fixture');

const ROOT = path.resolve(__dirname, '..');
const API = 'http://127.0.0.1:8717';
const EXTENSION = path.join(ROOT, 'extension').replaceAll('\\', '/');
const PROFILE = path.join(ROOT, `.demo-profile-${process.pid}-${Date.now()}`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let mock = null;
let context = null;
let stopping = false;

async function health() {
  try {
    const response = await fetch(`${API}/health`);
    return { ok: response.ok, body: await response.json() };
  } catch (_) {
    return { ok: false, body: null };
  }
}

async function startMock() {
  const existing = await health();
  if (existing.ok) {
    if (existing.body?.runtime !== 'mock') {
      throw new Error('127.0.0.1:8717 is already used by the real API. Stop it before starting the GPU-free demo.');
    }
    await fetch(`${API}/__test/reset`, { method: 'POST' });
    return;
  }

  mock = spawn(process.execPath, ['scripts/mock-voice-api.js'], {
    cwd: ROOT,
    stdio: 'inherit',
  });
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    const current = await health();
    if (current.ok && current.body?.runtime === 'mock') return;
    if (mock.exitCode !== null) break;
    await wait(150);
  }
  throw new Error('mock API did not become healthy');
}

async function cleanup() {
  if (stopping) return;
  stopping = true;
  if (context) await context.close().catch(() => {});
  if (mock && mock.exitCode === null) mock.kill();
  fs.rmSync(PROFILE, { recursive: true, force: true });
}

async function main() {
  fs.rmSync(PROFILE, { recursive: true, force: true });
  await startMock();

  context = await chromium.launchPersistentContext(PROFILE, {
    headless: false,
    viewport: { width: 1280, height: 720 },
    args: [
      `--disable-extensions-except=${EXTENSION}`,
      `--load-extension=${EXTENSION}`,
      '--autoplay-policy=no-user-gesture-required',
      '--no-first-run',
      '--mute-audio',
    ],
  });

  const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
  await worker.evaluate(async (apiUrl) => {
    await chrome.storage.local.clear();
    await chrome.storage.local.set({
      apiUrl: `${apiUrl}/v1/speak`,
      healthUrl: `${apiUrl}/health`,
      enabled: false,
      panelCollapsed: false,
      voiceVolume: 0,
      voiceId: '',
      referenceVoice: '',
    });
  }, API);

  const page = await context.newPage();
  await page.route('https://chatgpt.com/**', (route) => route.fulfill({
    status: 200,
    contentType: 'text/html; charset=utf-8',
    body: fixtureHtml(),
  }));
  await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
  await page.locator('#local-voice-bridge-panel').waitFor({ state: 'visible' });

  console.log('GPU-free local demo is open.');
  console.log('1. Turn Auto on in the Local Voice panel.');
  console.log('2. Confirm the existing reply is not read.');
  console.log('3. Click Send and try Next, Regen, and Replay.');
  console.log('Close the Chromium window or press Ctrl+C to stop the demo.');

  const autoCloseMs = Number(process.env.DEMO_AUTO_CLOSE_MS || 0);
  if (autoCloseMs > 0) setTimeout(() => context.close().catch(() => {}), autoCloseMs);
  await new Promise((resolve) => context.once('close', resolve));
}

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => cleanup().finally(() => process.exit(0)));
}

main()
  .catch((error) => {
    console.error(error.stack || error.message || String(error));
    process.exitCode = 1;
  })
  .finally(cleanup);
