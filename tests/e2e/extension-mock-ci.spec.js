const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { test, expect, chromium } = require('@playwright/test');
const { DEMO_REPLY, fixtureHtml } = require('../../scripts/demo-fixture');

const ROOT = path.resolve(__dirname, '../..');
const MOCK_PORT = Number(process.env.MOCK_VOICE_PORT || (18000 + (process.pid % 1000)));
const API = `http://127.0.0.1:${MOCK_PORT}`;
const SOURCE_EXTENSION = path.join(ROOT, 'extension');
const EXTENSION_DIR = path.join(os.tmpdir(), `local-voice-extension-mock-${process.pid}-${Date.now()}`);
const EXTENSION = EXTENSION_DIR.replaceAll('\\', '/');
const PROFILE = path.join(ROOT, `.e2e-profile-mock-${process.pid}-${Date.now()}`);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function prepareTestExtension() {
  fs.rmSync(EXTENSION_DIR, { recursive: true, force: true });
  fs.cpSync(SOURCE_EXTENSION, EXTENSION_DIR, { recursive: true });
  const manifestPath = path.join(EXTENSION_DIR, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.host_permissions = [...new Set([
    ...(Array.isArray(manifest.host_permissions) ? manifest.host_permissions : []),
    `http://127.0.0.1:${MOCK_PORT}/*`,
    `http://localhost:${MOCK_PORT}/*`,
  ])];
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, 'utf8');
}

test.beforeAll(() => prepareTestExtension());
test.afterAll(() => fs.rmSync(EXTENSION_DIR, { recursive: true, force: true }));

async function mockHealth() {
  try {
    const response = await fetch(`${API}/health`);
    const body = await response.json();
    return response.ok && body.runtime === 'mock';
  } catch (_) {
    return false;
  }
}

async function startMock() {
  try {
    const response = await fetch(`${API}/health`);
    if (response.ok) {
      const body = await response.json();
      if (body.runtime !== 'mock') throw new Error(`mock port ${MOCK_PORT} is already used by a non-mock API`);
      await fetch(`${API}/__test/reset`, { method: 'POST' });
      return null;
    }
  } catch (error) {
    if (String(error.message || error).includes('non-mock')) throw error;
  }

  const proc = spawn(process.execPath, ['scripts/mock-voice-api.js'], {
    cwd: ROOT,
    env: { ...process.env, MOCK_VOICE_PORT: String(MOCK_PORT) },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const until = Date.now() + 15000;
  while (Date.now() < until) {
    if (await mockHealth()) return proc;
    if (proc.exitCode !== null) break;
    await wait(150);
  }
  proc.kill();
  throw new Error(`mock API did not start on port ${MOCK_PORT}`);
}

async function apiEvents() {
  const response = await fetch(`${API}/__test/events`);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
  return body.events;
}

async function controlSnapshot() {
  const response = await fetch(`${API}/v1/control-panel`);
  expect(response.status).toBe(200);
  return response.json();
}

async function updateControlSettings(payload) {
  const response = await fetch(`${API}/v1/control-panel/settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
  return body;
}

async function sendControlCommand(command) {
  const response = await fetch(`${API}/v1/control-panel/command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command }),
  });
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
  return body;
}

async function sendConversationEvent(type, payload) {
  const response = await fetch(`${API}/v1/conversation/event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type, payload }),
  });
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
  return body;
}

async function waitForCounts(postCount, playbackCount) {
  await expect.poll(async () => {
    const events = await apiEvents();
    return {
      posts: events.filter((event) => event.method === 'POST' && event.path === '/v1/speak').length,
      playbacks: events.filter((event) => event.method === 'POST'
        && ['/v1/speak', '/v1/playback/replay'].includes(event.path)).length,
    };
  }, { timeout: 30000 }).toEqual({ posts: postCount, playbacks: playbackCount });
}

async function waitForControlReady(tabsCount = 1) {
  await expect.poll(async () => {
    const snapshot = await controlSnapshot();
    return {
      initialized: snapshot.initialized,
      connected: snapshot.extension.connected,
      tabsCount: snapshot.extension.tabsCount,
    };
  }, { timeout: 20000 }).toEqual({ initialized: true, connected: true, tabsCount });
}

async function launchContext() {
  fs.rmSync(PROFILE, { recursive: true, force: true });
  return chromium.launchPersistentContext(PROFILE, {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    channel: 'chromium',
    viewport: { width: 1280, height: 720 },
    args: [
      `--disable-extensions-except=${EXTENSION}`,
      `--load-extension=${EXTENSION}`,
      '--autoplay-policy=no-user-gesture-required',
      '--no-first-run',
      '--mute-audio',
    ],
  });
}

async function configureWorker(worker, values = {}) {
  await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('settingsVersion')).settingsVersion)).toBe(10);
  await worker.evaluate(async ({ apiUrl, healthUrl, overrides }) => {
    await chrome.storage.local.set({
      apiUrl,
      healthUrl,
      voiceVolume: 0,
      enabled: false,
      voiceId: '',
      referenceVoice: '',
      micConversationEnabled: false,
      sttModel: 'small',
      cancelGraceMs: 700,
      ...overrides,
    });
  }, { apiUrl: `${API}/v1/speak`, healthUrl: `${API}/health`, overrides: values });
}

function microphoneFixtureHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT microphone fixture</title></head><body>
    <main id="chat"></main>
    <textarea id="prompt-textarea"></textarea>
    <button data-testid="send-button" disabled>送信</button>
    <script>
      window.__sent = [];
      const composer = document.querySelector('#prompt-textarea');
      const send = document.querySelector('[data-testid="send-button"]');
      composer.addEventListener('input', () => { send.disabled = !composer.value.trim(); });
      send.addEventListener('click', () => {
        const text = composer.value.trim();
        if (!text || send.disabled) return;
        window.__sent.push(text);
        composer.value = '';
        composer.dispatchEvent(new Event('input', { bubbles: true }));
        const turn = document.createElement('article');
        turn.dataset.testid = 'conversation-turn-assistant-mic';
        const reply = document.createElement('div');
        reply.dataset.messageAuthorRole = 'assistant';
        reply.dataset.messageId = 'mic-reply-' + window.__sent.length;
        reply.textContent = '音声会話から送信された質問への返答です。';
        turn.append(reply);
        document.querySelector('#chat').append(turn);
      });
    </script>
  </body></html>`;
}

test('external panel controls Auto, Next, Regen, Replay, Ref, and excludes transient status text', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker);

    const page = await context.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fixtureHtml(),
    }));
    await page.goto('https://chatgpt.com/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#chat')).toBeVisible();
    await expect(page.locator('#local-voice-bridge-panel')).toHaveCount(0);
    await expect(page.locator('#local-voice-pixel-pet')).toHaveCount(0);
    await waitForControlReady(1);

    await updateControlSettings({ enabled: false, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => chrome.storage.local.get(['enabled', 'voiceVolume', 'voiceId', 'referenceVoice']))).toEqual({
      enabled: false,
      voiceVolume: 0,
      voiceId: 'sample',
      referenceVoice: 'sample',
    });
    await expect.poll(async () => {
      const petEvents = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/desktop-pet');
      return petEvents.at(-1)?.body?.petId || '';
    }).toBe('sample');

    await updateControlSettings({ enabled: true });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const thinking = document.createElement('div');
      thinking.dataset.messageAuthorRole = 'assistant';
      thinking.dataset.messageId = 'thinking-reply';
      thinking.textContent = '思考中';
      document.body.appendChild(thinking);
    });
    await page.waitForTimeout(1800);
    expect((await apiEvents()).filter((event) => event.path === '/v1/speak')).toHaveLength(0);
    await page.evaluate(() => document.querySelector('[data-message-id="thinking-reply"]')?.remove());

    await page.evaluate(() => {
      const analyzing = document.createElement('div');
      analyzing.dataset.messageAuthorRole = 'assistant';
      analyzing.dataset.messageId = 'analyzing-image-reply';
      analyzing.textContent = '画像を分析しています';
      document.body.appendChild(analyzing);
    });
    await page.waitForTimeout(1800);
    expect((await apiEvents()).filter((event) => event.path === '/v1/speak')).toHaveLength(0);
    await page.evaluate(() => document.querySelector('[data-message-id="analyzing-image-reply"]')?.remove());

    await page.evaluate(() => {
      const interrupted = document.createElement('div');
      interrupted.dataset.messageAuthorRole = 'assistant';
      interrupted.dataset.messageId = 'interrupted-image-reply';
      interrupted.textContent = '個の画像を分析していますストリーミングが中断されました。完全なメッセージを待機しています...';
      document.body.appendChild(interrupted);
    });
    await page.waitForTimeout(1800);
    expect((await apiEvents()).filter((event) => event.path === '/v1/speak')).toHaveLength(0);
    await page.evaluate(() => document.querySelector('[data-message-id="interrupted-image-reply"]')?.remove());

    await page.locator('#add-reply').click();
    await expect(page.locator('[data-message-id="new-reply"]')).toHaveText(DEMO_REPLY);
    await waitForCounts(1, 1);

    let events = await apiEvents();
    const firstPost = events.find((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(firstPost.body.referenceVoice).toBe('sample');
    expect(firstPost.body.voiceId).toBe('sample');
    expect(firstPost.body.text.length).toBeLessThanOrEqual(80);
    expect(firstPost.body.text).not.toBe(DEMO_REPLY);
    await expect.poll(async () => (await controlSnapshot()).extension.currentText).toContain('これはオートをオンにした後に届いた新しい返答です。');

    await sendControlCommand('next');
    await waitForCounts(2, 2);
    events = await apiEvents();
    const postsAfterNext = events.filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(postsAfterNext[1].body.text).not.toBe(postsAfterNext[0].body.text);

    await sendControlCommand('regen');
    await waitForCounts(3, 3);
    events = await apiEvents();
    const postsAfterRegen = events.filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(postsAfterRegen[2].body.text).toBe(postsAfterRegen[1].body.text);

    await sendControlCommand('replay');
    await waitForCounts(3, 4);
    expect(await page.locator('#local-voice-bridge-panel').count()).toBe(0);
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Next uses the completed streaming reply instead of the short Auto preview snapshot', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker);
    const page = await context.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fixtureHtml(),
    }));
    await page.goto('https://chatgpt.com/c/streaming-next', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'streaming-next-reply';
      reply.textContent = '概ね妥当です。';
      document.querySelector('#chat').append(reply);
    });
    await waitForCounts(1, 1);

    await page.evaluate(() => {
      document.querySelector('[data-message-id="streaming-next-reply"]').textContent =
        '概ね妥当です。\nただし、公開時の誤認防止とブランド統一のために変更すべき項目があります。\nChrome拡張名、EXE名、スタートメニュー名、READMEタイトルを独自名称へ統一します。';
    });
    await page.waitForTimeout(700);

    await sendControlCommand('next');
    await waitForCounts(2, 2);
    const posts = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(posts[1].body.text).not.toBe('概ね妥当です。');
    expect(posts[1].body.text).toContain('ただし');
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('all ChatGPT tabs continue to enqueue into one Auto queue without an in-page panel', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker, { voiceId: 'sample', referenceVoice: 'sample' });

    const pages = [await context.newPage(), await context.newPage()];
    for (let index = 0; index < pages.length; index += 1) {
      await pages[index].route('https://chatgpt.com/**', (route) => route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fixtureHtml(),
      }));
      await pages[index].goto(`https://chatgpt.com/c/mock-${index}`, { waitUntil: 'domcontentloaded' });
      await expect(pages[index].locator('#chat')).toBeVisible();
      await expect(pages[index].locator('#local-voice-bridge-panel')).toHaveCount(0);
    }
    await waitForControlReady(2);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await pages[0].locator('#add-reply').click();
    await pages[1].locator('#add-reply').click();
    await waitForCounts(2, 2);

    const posts = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(posts.map((event) => event.body.referenceVoice)).toEqual(['sample', 'sample']);
    expect((await controlSnapshot()).extension.tabsCount).toBe(2);
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('a completed reply marks its background tab until the user focuses it', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const backgroundPage = await context.newPage();
    const foregroundPage = await context.newPage();
    for (const [index, page] of [backgroundPage, foregroundPage].entries()) {
      await page.route('https://chatgpt.com/**', (route) => route.fulfill({
        status: 200,
        contentType: 'text/html; charset=utf-8',
        body: fixtureHtml(),
      }));
      await page.goto(`https://chatgpt.com/c/completion-marker-${index}`, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#chat')).toBeVisible();
    }

    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker, { voiceId: 'sample', referenceVoice: 'sample' });
    await waitForControlReady(2);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await foregroundPage.bringToFront();
    await backgroundPage.evaluate(() => {
      const stopButton = document.createElement('button');
      stopButton.dataset.testid = 'stop-button';
      stopButton.textContent = 'Stop generating';
      document.body.append(stopButton);

      const turn = document.createElement('article');
      turn.dataset.testid = 'conversation-turn-assistant-completion-marker';
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'completion-marker-reply';
      reply.textContent = 'バックグラウンドタブで完了した返答です。';
      turn.append(reply);
      document.querySelector('#chat').append(turn);
    });

    await waitForCounts(1, 1);
    await backgroundPage.waitForTimeout(1200);
    expect(await backgroundPage.title()).toBe('Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);

    await backgroundPage.evaluate(() => document.querySelector('[data-testid="stop-button"]')?.remove());
    await expect.poll(() => backgroundPage.title(), { timeout: 10000 }).toBe('● Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveAttribute('href', /^data:image\/svg\+xml,/);
    expect(await foregroundPage.title()).toBe('Local Voice Demo Fixture');
    await expect(foregroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);

    await backgroundPage.bringToFront();
    await expect.poll(() => backgroundPage.title(), { timeout: 5000 }).toBe('Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('microphone transcript supports Esc cancellation, 0.7 second auto-send, and reply TTS', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker);
    const page = await context.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: microphoneFixtureHtml(),
    }));
    await page.goto('https://chatgpt.com/c/microphone', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#prompt-textarea')).toBeVisible();
    await waitForControlReady(1);

    const extensionId = new URL(worker.url()).host;
    const optionsPage = await context.newPage();
    await optionsPage.goto(`chrome-extension://${extensionId}/options.html`);
    await optionsPage.locator('#stt-model').selectOption('small');
    await optionsPage.locator('#cancel-grace-seconds').fill('1.2');
    await optionsPage.locator('button[type="submit"]').click();
    await expect(optionsPage.locator('#save-status')).toHaveText('設定を保存しました');
    await optionsPage.close();
    await updateControlSettings({
      enabled: true,
      micConversationEnabled: true,
      voiceVolume: 0,
      referenceVoice: '',
    });
    await expect.poll(async () => worker.evaluate(async () => chrome.storage.local.get([
      'enabled', 'micConversationEnabled', 'sttModel', 'cancelGraceMs',
    ]))).toEqual({
      enabled: true,
      micConversationEnabled: true,
      sttModel: 'small',
      cancelGraceMs: 1200,
    });

    await sendConversationEvent('transcript', {
      sessionId: 1,
      text: 'Escでキャンセルする音声入力',
      cancelGraceMs: 1200,
    });
    await expect(page.locator('#prompt-textarea')).toHaveValue('Escでキャンセルする音声入力');
    await expect(page.locator('#local-voice-cancel-hint')).toContainText('Escでキャンセル');
    await page.keyboard.press('Escape');
    await expect(page.locator('#prompt-textarea')).toHaveValue('');
    await expect(page.locator('#local-voice-cancel-hint')).toHaveCount(0);
    expect(await page.evaluate(() => window.__sent)).toEqual([]);

    await sendConversationEvent('transcript', {
      sessionId: 2,
      text: '0.7秒後に一度だけ送信する音声入力',
      cancelGraceMs: 700,
    });
    await expect(page.locator('#prompt-textarea')).toHaveValue('0.7秒後に一度だけ送信する音声入力');
    await expect(page.locator('#local-voice-cancel-hint')).toContainText('0.7秒');
    await expect.poll(() => page.evaluate(() => window.__sent.length), { timeout: 5000 }).toBe(1);
    expect(await page.evaluate(() => window.__sent[0])).toBe('0.7秒後に一度だけ送信する音声入力');
    await page.waitForTimeout(1000);
    expect(await page.evaluate(() => window.__sent.length)).toBe(1);
    await expect(page.locator('[data-message-id="mic-reply-1"]')).toHaveText('音声会話から送信された質問への返答です。');
    await waitForCounts(1, 1);

    await page.evaluate(() => {
      const composer = document.querySelector('#prompt-textarea');
      composer.value = '既存入力を保持する';
      composer.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await sendConversationEvent('transcript', {
      sessionId: 3,
      text: '上書きしてはいけない音声入力',
      cancelGraceMs: 700,
    });
    await page.waitForTimeout(1200);
    await expect(page.locator('#prompt-textarea')).toHaveValue('既存入力を保持する');
    expect(await page.evaluate(() => window.__sent.length)).toBe(1);
    await expect.poll(async () => (await controlSnapshot()).conversation.phase).toBe('error');
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto reads a complete assistant reply shorter than 20 characters from the external setting', async () => {
  test.setTimeout(90000);
  const api = await startMock();
  const context = await launchContext();

  try {
    const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
    await configureWorker(worker);
    const page = await context.newPage();
    await page.route('https://chatgpt.com/**', (route) => route.fulfill({
      status: 200,
      contentType: 'text/html; charset=utf-8',
      body: fixtureHtml(),
    }));
    await page.goto('https://chatgpt.com/c/short', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#chat')).toBeVisible();
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: '' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const turn = document.createElement('article');
      turn.dataset.testid = 'conversation-turn-assistant-short';
      const message = document.createElement('div');
      message.dataset.messageAuthorRole = 'assistant';
      message.dataset.messageId = 'short-reply';
      message.textContent = 'はい、返事できます。';
      turn.append(message);
      document.body.append(turn);
    });

    await waitForCounts(1, 1);
    const posts = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(posts[0].body.text).toBe('はい、返事できます。');
    await expect(page.locator('#local-voice-bridge-panel')).toHaveCount(0);
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});
