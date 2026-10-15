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
const PROFILE_DIR = path.join(ROOT, `.e2e-profile-ref-pet-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
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

function html() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT Ref Pet Fixture</title></head><body><main><h1>ChatGPT fixture</h1></main><script>window.addAssistant=()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-2');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','ref-pet-msg');msg.textContent='準備中';turn.appendChild(msg);document.querySelector('main').appendChild(turn);setTimeout(()=>{msg.textContent='これはPlaywrightによる拡張機能E2Eテストです。ローカル音声APIで音声を生成し、ブラウザ上で再生完了まで確認します。';},700);};window.addLongAssistant=()=>{const turn=document.createElement('div');turn.setAttribute('data-testid','conversation-turn-3');const msg=document.createElement('div');msg.setAttribute('data-message-author-role','assistant');msg.setAttribute('data-message-id','ref-pet-long-msg');msg.textContent='準備中';turn.appendChild(msg);document.querySelector('main').appendChild(turn);setTimeout(()=>{msg.textContent='最初のプレビューです。' + 'あ'.repeat(200) + '次のプレビューです。';},700);};</script></body></html>`;
}

test('Ref list, desktop pet selection, storage sync, and speak payload all use the selected reference voice', async () => {
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

  const speakResponses = [];
  ctx.on('response', async (res) => {
    if (!res.url().startsWith(`${API_BASE}/v1/speak`)) return;
    const body = await res.json().catch(() => null);
    speakResponses.push({ status: res.status(), body });
  });

  try {
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker);
    const page = await ctx.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });

    const panel = page.locator('#local-voice-bridge-panel');
    await expect(panel).toBeVisible({ timeout: 30000 });
    if (!(await panel.locator('[data-local-voice-field="ref"]').isVisible().catch(() => false))) {
      await panel.locator(':scope > div').first().click();
    }
    await expect(panel.getByRole('button', { name: 'Regen' })).toBeVisible({ timeout: 10000 });

    const refSelect = panel.locator('[data-testid="local-voice-ref"]');
    await expect(panel.locator('select')).toHaveCount(1);
    await expect(panel.getByText('Voice', { exact: true })).toHaveCount(0);
    await expect(panel.getByText('Tab', { exact: true })).toHaveCount(0);
    await expect(panel.getByText('Pet', { exact: true })).toHaveCount(0);
    await expect(refSelect).toBeVisible();
    await expect
      .poll(async () => await refSelect.evaluate((el) => Array.from(el.options).map((opt) => opt.value).filter(Boolean).length))
      .toBeGreaterThan(0);
    await refSelect.selectOption({ index: 1 });
    const selectedReference = await refSelect.inputValue();

    await expect
      .poll(async () => await worker.evaluate(async () => chrome.storage.local.get(['voiceId', 'referenceVoice'])))
      .toEqual({ voiceId: selectedReference, referenceVoice: selectedReference });
    await expect.poll(async () => {
      const payload = await fetch(`${API_BASE}/v1/desktop-pet`).then((response) => response.json());
      return payload.selectedPetId;
    }).toBe(selectedReference);

    await expect(page.locator('#local-voice-pixel-pet')).toHaveCount(0);

    await page.evaluate(() => window.addAssistant());
    await expect(page.locator('[data-message-id="ref-pet-msg"]')).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(2500);
    await panel.getByRole('button', { name: 'Regen' }).click();
    await expect
      .poll(() => speakResponses.length)
      .toBeGreaterThan(0);
    expect(speakResponses.at(-1)).toMatchObject({
      status: 200,
      body: {
        ok: true,
        voiceId: expect.any(String),
        referenceVoice: expect.any(String),
      },
    });

    await page.evaluate(() => window.addLongAssistant());
    await expect(page.locator('[data-message-id="ref-pet-long-msg"]')).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(2500);
    const speakCountBeforeNext = speakResponses.length;
    await panel.getByRole('button', { name: 'Next' }).click();
    await expect.poll(() => speakResponses.length, { timeout: 180000 }).toBe(speakCountBeforeNext + 1);
    expect(speakResponses.at(-1)).toMatchObject({
      status: 200,
      body: {
        ok: true,
        voiceId: expect.any(String),
        referenceVoice: expect.any(String),
      },
    });
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});

test('two ChatGPT tabs keep a real reference voice through the continuous Auto queue', async () => {
  test.setTimeout(300000);
  const api = await startApi();
  const referencePayload = await fetch(`${API_BASE}/v1/reference-voices`).then((response) => response.json());
  const referenceVoice = (referencePayload.voices || []).map((voice) => String(voice.id || '')).find(Boolean);
  expect(referenceVoice).toBeTruthy();

  fs.rmSync(PROFILE_DIR, { recursive: true, force: true });
  const loadArg = '--' + 'load-extension=' + EXTENSION_ARG;
  const onlyArg = '--' + 'disable-extensions-except=' + EXTENSION_ARG;
  const ctx = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    args: [onlyArg, loadArg, '--autoplay-policy=no-user-gesture-required', '--no-first-run', '--mute-audio'],
  });

  const speakResponses = [];
  const audioResponses = [];
  ctx.on('response', async (res) => {
    if (res.url().startsWith(`${API_BASE}/v1/speak`)) {
      speakResponses.push({ status: res.status(), body: await res.json().catch(() => null) });
    }
    if (res.url().startsWith(`${API_BASE}/audio/`) && res.status() === 200) audioResponses.push(res.url());
  });

  try {
    const worker = ctx.serviceWorkers()[0] || await ctx.waitForEvent('serviceworker', { timeout: 15000 });
    await configureExtension(worker, {
      enabled: false,
      panelCollapsed: false,
    });

    const pages = [await ctx.newPage(), await ctx.newPage()];
    for (const page of pages) {
      await page.route('https://chatgpt.com/**', (route) => route.fulfill({ status: 200, contentType: 'text/html', body: html() }));
    }
    await Promise.all(pages.map((page, index) => page.goto(`https://chatgpt.com/c/ref-queue-${index}`, { waitUntil: 'domcontentloaded' })));

    await pages[0].bringToFront();
    await expect(pages[0].locator('#local-voice-bridge-panel')).toBeVisible({ timeout: 30000 });
    await expect(pages[1].locator('#local-voice-bridge-panel')).toBeHidden();
    const firstPanel = pages[0].locator('#local-voice-bridge-panel');
    if (!(await firstPanel.locator('[data-local-voice-field="ref"]').isVisible().catch(() => false))) {
      await firstPanel.locator(':scope > div').first().click();
    }
    const referenceSelect = firstPanel.locator('[data-testid="local-voice-ref"]');
    await expect(firstPanel.locator('select')).toHaveCount(1);
    await expect.poll(async () => referenceSelect.locator(`option[value="${referenceVoice}"]`).count(), { timeout: 30000 }).toBe(1);
    await referenceSelect.selectOption(referenceVoice);
    await expect.poll(async () => worker.evaluate(async () => chrome.storage.local.get(['voiceId', 'referenceVoice'])))
      .toEqual({ voiceId: referenceVoice, referenceVoice });
    await firstPanel.getByRole('button', { name: 'Auto' }).click();
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);
    await pages[0].evaluate(() => window.addAssistant());
    await expect.poll(() => speakResponses.length, { timeout: 240000 }).toBe(1);

    await pages[1].bringToFront();
    await expect(pages[1].locator('#local-voice-bridge-panel')).toBeVisible({ timeout: 30000 });
    await expect(pages[0].locator('#local-voice-bridge-panel')).toBeHidden();
    await pages[1].evaluate(() => window.addAssistant());
    await expect.poll(() => speakResponses.length, { timeout: 240000 }).toBe(2);
    await expect.poll(() => audioResponses.length, { timeout: 60000 }).toBe(2);
    expect(speakResponses.every((entry) => entry.status === 200)).toBe(true);
    expect(speakResponses.map((entry) => entry.body.referenceVoice)).toEqual([referenceVoice, referenceVoice]);
    expect(speakResponses.every((entry) => Boolean(entry.body.usedReferenceAudio))).toBe(true);
  } finally {
    await ctx.close().catch(() => {});
    if (api) api.kill();
  }
});
