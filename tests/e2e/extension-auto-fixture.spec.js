const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { test, expect, chromium } = require('@playwright/test');

const ROOT = path.resolve(__dirname, '../..');
const API_BASE = 'http://127.0.0.1:8717';
const EXTENSION_DIR = path.join(ROOT, 'extension');
const EXTENSION_ARG = EXTENSION_DIR.split(String.fromCharCode(92)).join('/');
const PROFILE_DIR = path.join(ROOT, `.e2e-profile-auto-fixture-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
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
  const p = spawn(python, ['server.py'], {
    cwd: path.join(ROOT, 'local-api'),
    env: { ...process.env, LOCAL_VOICE_PORT: '8717', LOCAL_VOICE_PUBLIC_BASE_URL: API_BASE },
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

async function configureExtension(worker) {
  await worker.evaluate(async ({ apiBase }) => {
    await chrome.storage.local.set({
      apiUrl: apiBase + '/v1/speak',
      healthUrl: apiBase + '/health',
      model: 'irodori-v3',
      voiceProfile: 'irodori-v3',
      voiceId: '',
      referenceVoice: '',
      voiceVolume: 0,
      enabled: false,
    });
  }, { apiBase: API_BASE });
}

function html() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT Auto Fixture</title></head><body><main><h1>ChatGPT fixture</h1><form id="composer"><textarea id="prompt-textarea"></textarea><button type="submit" data-testid="send-button">Send</button></form></main><script>document.querySelector('#composer').addEventListener('submit',(event)=>{event.preventDefault();const userTurn=document.createElement('div');userTurn.setAttribute('data-testid','conversation-turn-user');userTurn.innerHTML='<div data-message-author-role="user">'+document.querySelector('#prompt-textarea').value+'</div>';document.querySelector('main').appendChild(userTurn);setTimeout(()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-assistant');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','auto-e2e-msg');msg.textContent='これは自動読み上げE2Eの返答です。ChatGPTの返答DOMが出たタイミングで読み上げます。';turn.appendChild(msg);document.querySelector('main').appendChild(turn);},1000);});</script></body></html>`;
}

test('auto mode reads when assistant reply appears after message send', async () => {
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
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker);
    console.log(`[e2e] worker ${worker.url()}`);
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
    await page.locator('#prompt-textarea').fill('音声読み上げE2Eを実行してください。');
    await page.locator('[data-testid="send-button"]').click();
    await expect(page.locator('[data-message-author-role="assistant"]').last()).toBeVisible({ timeout: 30000 });
    await expect(panel).toContainText('Played chunk', { timeout: 180000 });
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});
