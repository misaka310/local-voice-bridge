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
const PROFILE_DIR = path.join(ROOT, `.e2e-profile-multichunk-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test.afterEach(() => {
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
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
  const proc = spawn(python, ['server.py'], {
    cwd: path.join(ROOT, 'local-api'),
    env: { ...process.env, LOCAL_VOICE_PORT: API_PORT, LOCAL_VOICE_PUBLIC_BASE_URL: PUBLIC_BASE },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  proc.stdout.on('data', (data) => process.stdout.write(`[local-api] ${data}`));
  proc.stderr.on('data', (data) => process.stderr.write(`[local-api] ${data}`));
  const until = Date.now() + 45000;
  while (Date.now() < until) {
    if (await healthy()) return proc;
    await wait(1000);
  }
  if (proc.exitCode === null) proc.kill();
  throw new Error('local api did not become healthy');
}

async function configureExtension(worker) {
  await worker.evaluate(async ({ apiBase }) => {
    await chrome.storage.local.set({
      apiUrl: `${apiBase}/v1/speak`,
      healthUrl: `${apiBase}/health`,
      model: 'irodori-v3',
      voiceProfile: 'irodori-v3',
      voiceId: '',
      referenceVoice: '',
      voiceVolume: 0,
      enabled: false,
    });
  }, { apiBase: API_BASE });
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
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT Multi Chunk Fixture</title></head><body><main><h1>ChatGPT fixture</h1></main><script>window.addMultiChunkConversation=(promptText)=>{const userTurn=document.createElement('div');userTurn.setAttribute('data-testid','conversation-turn-user');userTurn.innerHTML='<div data-message-author-role="user">'+promptText+'</div>';document.querySelector('main').appendChild(userTurn);setTimeout(()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-assistant');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','multichunk-e2e-msg');msg.textContent='${'あ'.repeat(260)}終わりです。';turn.appendChild(msg);document.querySelector('main').appendChild(turn);},1000);};window.addSixLineConversation=()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-lines');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','six-line-e2e-msg');msg.textContent='';turn.appendChild(msg);document.querySelector('main').appendChild(turn);setTimeout(()=>{msg.textContent='1行目です。\\n2行目です。\\n3行目です。\\n4行目です。\\n5行目です。\\n6行目です。';},700);};</script></body></html>`;
}

test('auto mode reads only the preview of a long assistant reply and returns to Queue empty', async () => {
  test.setTimeout(240000);
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
    const autoButton = panel.getByRole('button', { name: 'Auto' });
    await expect(autoButton).toBeVisible({ timeout: 10000 });
    await autoButton.click();
    await expect(panel).toContainText('Auto read enabled', { timeout: 10000 });
    await page.evaluate(() => window.addMultiChunkConversation('複数 chunk の読み上げキューを確認します。'));
    await expect(page.locator('[data-message-author-role="assistant"]').last()).toBeVisible({ timeout: 30000 });
    await expect(panel).toContainText('Played chunk 1/4', { timeout: 180000 });
    await expect(panel).toContainText('Queue empty', { timeout: 30000 });
    await page.waitForTimeout(3000);
    const panelText = await panel.textContent();
    expect(panelText).not.toMatch(/Played chunk [2-9]\d*\/[2-9]\d*/);
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});

test('Auto reads two lines and each Next advances by the next two lines without repeating the end', async () => {
  test.setTimeout(240000);
  const api = await startApi();
  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });
  const speakRequests = [];
  ctx.on('request', (request) => {
    if (!request.url().startsWith(`${API_BASE}/v1/speak`)) return;
    const payload = request.postDataJSON();
    if (payload) speakRequests.push(payload);
  });
  try {
    await routeDefaultApiToTestServer(ctx);
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker);
    await worker.evaluate(async () => {
      await chrome.storage.local.set({ previewMinChars: 1, previewStableMs: 100 });
    });
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    await panel.click({ position: { x: 40, y: 12 } });
    await panel.getByRole('button', { name: 'Auto' }).click();
    await expect(panel).toContainText('Auto read enabled', { timeout: 10000 });

    await page.evaluate(() => window.addSixLineConversation());
    await expect(page.locator('[data-message-id="six-line-e2e-msg"]')).toContainText('6行目です。', { timeout: 30000 });
    await expect(panel).toContainText('Played chunk 1/3', { timeout: 180000 });
    expect(speakRequests.at(-1).text).toBe('1行目です。 2行目です。');

    await panel.getByRole('button', { name: 'Next' }).click();
    await expect(panel).toContainText('Played chunk 2/3', { timeout: 180000 });
    expect(speakRequests.at(-1).text).toBe('3行目です。 4行目です。');

    await panel.getByRole('button', { name: 'Next' }).click();
    await expect(panel).toContainText('Played chunk 3/3', { timeout: 180000 });
    expect(speakRequests.at(-1).text).toBe('5行目です。 6行目です。');

    const requestCountAtEnd = speakRequests.length;
    await panel.getByRole('button', { name: 'Next' }).click();
    await expect(panel).toContainText('End of response', { timeout: 10000 });
    await page.waitForTimeout(1000);
    expect(speakRequests).toHaveLength(requestCountAtEnd);
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});
