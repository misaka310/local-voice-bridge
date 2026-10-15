const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { test, expect, chromium } = require('@playwright/test');

const ROOT = path.resolve(__dirname, '../..');
const API_BASE = process.env.LOCAL_VOICE_API_BASE || 'http://127.0.0.1:8717';
const PUBLIC_BASE = process.env.LOCAL_VOICE_PUBLIC_BASE_URL || API_BASE;
const API_PORT = new URL(API_BASE).port || '80';
const EXTENSION_DIR = path.join(ROOT, 'extension');
const EXTENSION_ARG = EXTENSION_DIR.split(String.fromCharCode(92)).join('/');
const PROFILE_DIR = path.join(ROOT, '.e2e-profile-playback-' + process.pid + '-' + Date.now() + '-' + Math.random().toString(36).slice(2));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let playbackNetworkEvents = null;

test.afterEach(() => {
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
});

test.afterEach(async ({}, testInfo) => {
  if (testInfo.title !== 'extension playback generates, fetches, and finishes one chunk') return;
  const events = playbackNetworkEvents || [];
  const speakResponse = events.find((event) => event.method === 'POST' && new URL(event.url).pathname === '/v1/speak');
  const audioResponse = events.find((event) => event.method === 'GET' && new URL(event.url).pathname.startsWith('/audio/'));
  expect(speakResponse).toBeDefined();
  expect(speakResponse.status).toBe(200);
  expect(audioResponse).toBeDefined();
  expect(audioResponse.status).toBe(200);
  playbackNetworkEvents = null;
});

async function healthy() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    const body = await res.json();
    return res.ok && body.ok === true;
  } catch (_) {
    return false;
  }
}

async function startApi() {
  if (await healthy()) return null;
  const python = path.join(ROOT, 'local-api', '.venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(python)) throw new Error(`missing venv python: ${python}`);
  const p = spawn(python, ['server.py'], {
    cwd: path.join(ROOT, 'local-api'),
    env: { ...process.env, LOCAL_VOICE_PORT: API_PORT, LOCAL_VOICE_PUBLIC_BASE_URL: PUBLIC_BASE },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  p.stdout.on('data', (d) => process.stdout.write(`[local-api] ${d}`));
  p.stderr.on('data', (d) => process.stderr.write(`[local-api] ${d}`));
  const until = Date.now() + 45000;
  while (Date.now() < until) {
    if (await healthy()) return p;
    await wait(1000);
  }
  if (p.exitCode === null) p.kill();
  throw new Error('local api did not become healthy');
}

async function configureExtension(worker, overrides = {}) {
  await worker.evaluate(async ({ apiBase, overrides: nextOverrides }) => {
    await chrome.storage.local.set({
      apiUrl: `${apiBase}/v1/speak`,
      healthUrl: `${apiBase}/health`,
      model: 'irodori-v3',
      voiceProfile: 'irodori-v3',
      voiceId: '',
      referenceVoice: '',
      voiceVolume: 0,
      enabled: false,
      ...nextOverrides,
    });
  }, { apiBase: API_BASE, overrides });
}

async function routeDefaultApiToTestServer(ctx) {
  if (API_BASE === 'http://127.0.0.1:8717') return;
  const rewrite = (url) => url
    .replace('http://127.0.0.1:8717', API_BASE)
    .replace('http://localhost:8717', API_BASE);
  await ctx.route('http://127.0.0.1:8717/**', (route) => route.continue({ url: rewrite(route.request().url()) }));
  await ctx.route('http://localhost:8717/**', (route) => route.continue({ url: rewrite(route.request().url()) }));
}

function html() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT E2E</title></head><body><main><h1>ChatGPT fixture</h1></main><script>window.addAssistant=()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-2');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','e2e-msg');msg.textContent='準備中';turn.appendChild(msg);document.querySelector('main').appendChild(turn);setTimeout(()=>{msg.textContent='これはPlaywrightによる拡張機能E2Eテストです。ローカル音声APIで音声を生成し、ブラウザ上で再生完了まで確認します。';},700);};</script></body></html>`;
}

function htmlForNextTwoChunks() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT Next Chunk Fixture</title></head><body><main><h1>ChatGPT fixture</h1></main><script>window.addAssistant=()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-2');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','next-two-chunks-msg');msg.textContent='生成中';turn.appendChild(msg);document.querySelector('main').appendChild(turn);setTimeout(()=>{msg.textContent=${JSON.stringify('1行目: 明日の新宿の気温を要点だけ短く整理して、朝から夜までの変化をざっくり確認します。\n2行目: 最高気温と最低気温と天気の見通しに加えて、服装の目安まで順番に確認してください。')};},700);};</script></body></html>`;
}

test('extension playback generates, fetches, and finishes one chunk', async () => {
  test.setTimeout(210000);
  const api = await startApi();
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });
  const apiEvents = [];
  playbackNetworkEvents = apiEvents;
  try {
    await routeDefaultApiToTestServer(ctx);
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    console.log(`[e2e] worker ${worker.url()}`);
    await configureExtension(worker);
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    ctx.on('response', (res) => {
      if (res.url().startsWith(API_BASE)) {
        apiEvents.push({ url: res.url(), status: res.status(), method: res.request().method() });
      }
    });
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    await panel.click({ position: { x: 40, y: 12 } });
    await expect(panel.getByRole('button', { name: 'Regen' })).toBeVisible({ timeout: 10000 });
    await page.evaluate(() => window.addAssistant());
    await page.waitForTimeout(2500);
    await panel.getByRole('button', { name: 'Regen' }).click();
    await expect(panel).toContainText('Played chunk 1/1', { timeout: 180000 });
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});

test('stale reference voice storage is normalized to empty before speak', async () => {
  test.setTimeout(210000);
  const api = await startApi();
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });
  try {
    await routeDefaultApiToTestServer(ctx);
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker, { voiceId: 'qwen3', referenceVoice: 'qwen3' });
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    await panel.click({ position: { x: 40, y: 12 } });
    await expect(panel.getByRole('button', { name: 'Regen' })).toBeVisible({ timeout: 10000 });
    await page.evaluate(() => window.addAssistant());
    await page.waitForTimeout(2500);
    await panel.getByRole('button', { name: 'Regen' }).click();
    await expect(panel).toContainText('Played chunk 1/1', { timeout: 180000 });
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});

test('Next advances to the second preview chunk when the reply spans two chunks', async () => {
  test.setTimeout(210000);
  const api = await startApi();
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });
  try {
    await routeDefaultApiToTestServer(ctx);
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker);
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: htmlForNextTwoChunks() }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    await panel.click({ position: { x: 40, y: 12 } });
    await expect(panel.getByRole('button', { name: 'Next' })).toBeVisible({ timeout: 10000 });
    await page.evaluate(() => window.addAssistant());
    await page.waitForTimeout(2500);
    await panel.getByRole('button', { name: 'Regen' }).click();
    await expect(panel).toContainText('Played chunk 1/2', { timeout: 180000 });
    await panel.getByRole('button', { name: 'Next' }).click();
    await expect(panel).toContainText('Played chunk 2/2', { timeout: 180000 });
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});

test('collapsed panel shows the current status only once', async () => {
  test.setTimeout(210000);
  const api = await startApi();
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });
  try {
    await routeDefaultApiToTestServer(ctx);
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker);
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    await panel.click({ position: { x: 40, y: 12 } });
    await expect(panel.getByRole('button', { name: 'Regen' })).toBeVisible({ timeout: 10000 });
    await page.evaluate(() => window.addAssistant());
    await page.waitForTimeout(2500);
    await panel.getByRole('button', { name: 'Regen' }).click();
    await expect(panel).toContainText('Played chunk 1/1', { timeout: 180000 });
    await panel.locator('div').first().click();
    await page.waitForTimeout(300);
    const text = (await panel.textContent()) || '';
    expect((text.match(/Ready/g) || []).length).toBe(1);
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});
