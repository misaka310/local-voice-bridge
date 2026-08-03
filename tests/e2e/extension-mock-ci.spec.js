const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');
const { spawn } = require('child_process');
const { test, expect, chromium } = require('@playwright/test');
const { DEMO_REPLY, fixtureHtml } = require('../../scripts/demo-fixture');

const ROOT = path.resolve(__dirname, '../..');
const FIXED_MOCK_PORT = Number(process.env.MOCK_VOICE_PORT || 0);
let MOCK_PORT = 0;
let API = '';
const SOURCE_EXTENSION = path.join(ROOT, 'extension');
const EXTENSION_DIR = path.join(os.tmpdir(), `local-voice-extension-mock-${process.pid}-${Date.now()}`);
const EXTENSION = EXTENSION_DIR.replaceAll('\\', '/');
const PROFILE = process.env.LOCAL_VOICE_TEST_PROFILE
  ? path.resolve(process.env.LOCAL_VOICE_TEST_PROFILE)
  : path.join(ROOT, `.e2e-profile-mock-${process.pid}-${Date.now()}`);
const BROWSER_EXECUTABLE = String(process.env.LOCAL_VOICE_BROWSER_EXECUTABLE || '').trim();
const MOCK_RUN_TOKEN = crypto.randomUUID();
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function mockFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set('X-Local-Voice-Test-Token', MOCK_RUN_TOKEN);
  return fetch(url, { ...options, headers });
}
const USED_MOCK_PORTS = new Set();
const MOCK_PORT_MIN = 52000;
const MOCK_PORT_RANGE = 10000;
const MOCK_PORT_BASE = MOCK_PORT_MIN + ((process.pid * 997 + Date.now()) % MOCK_PORT_RANGE);
const MOCK_PORT_RESERVATION_DIR = path.join(os.tmpdir(), 'local-voice-e2e-port-reservations');
const MOCK_PORT_RESERVATION_TTL_MS = 12 * 60 * 60 * 1000;
let nextMockPortOffset = 0;

function prepareTestExtension() {
  fs.rmSync(EXTENSION_DIR, { recursive: true, force: true });
  fs.cpSync(SOURCE_EXTENSION, EXTENSION_DIR, { recursive: true });
  const settingsPath = path.join(EXTENSION_DIR, 'background-settings-core.js');
  const settingsSource = fs.readFileSync(settingsPath, 'utf8');
  const isolatedSettingsSource = settingsSource.replaceAll('http://127.0.0.1:8717', 'http://127.0.0.1:1');
  if (isolatedSettingsSource === settingsSource) throw new Error('test extension defaults were not isolated from the real local API');
  fs.writeFileSync(settingsPath, isolatedSettingsSource, 'utf8');
  const backgroundPath = path.join(EXTENSION_DIR, 'background.js');
  const backgroundSource = fs.readFileSync(backgroundPath, 'utf8');
  const fetchShim = `
const __localVoiceTestToken = ${JSON.stringify(MOCK_RUN_TOKEN)};
const __localVoiceTestFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = (input, init = {}) => {
  const headers = new Headers(init.headers || {});
  headers.set('X-Local-Voice-Test-Token', __localVoiceTestToken);
  return __localVoiceTestFetch(input, { ...init, headers });
};
`;
  fs.writeFileSync(backgroundPath, `${fetchShim}${backgroundSource}`, 'utf8');
  const manifestPath = path.join(EXTENSION_DIR, 'manifest.json');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  manifest.host_permissions = [...new Set([
    ...(Array.isArray(manifest.host_permissions) ? manifest.host_permissions : []),
    'http://127.0.0.1/*',
    'http://localhost/*',
  ])];
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, 'utf8');
}

test.beforeAll(() => prepareTestExtension());
test.afterAll(() => fs.rmSync(EXTENSION_DIR, { recursive: true, force: true }));

function reserveMockPort(port) {
  fs.mkdirSync(MOCK_PORT_RESERVATION_DIR, { recursive: true });
  const reservationPath = path.join(MOCK_PORT_RESERVATION_DIR, String(port));
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const descriptor = fs.openSync(reservationPath, 'wx');
      try {
        fs.writeFileSync(descriptor, `${Date.now()}\n`, 'utf8');
      } finally {
        fs.closeSync(descriptor);
      }
      return true;
    } catch (error) {
      if (!error || error.code !== 'EEXIST') throw error;
      try {
        const ageMs = Date.now() - fs.statSync(reservationPath).mtimeMs;
        if (ageMs > MOCK_PORT_RESERVATION_TTL_MS) {
          fs.rmSync(reservationPath, { force: true });
          continue;
        }
      } catch (statError) {
        if (!statError || statError.code !== 'ENOENT') throw statError;
        continue;
      }
      return false;
    }
  }
  return false;
}

async function allocateMockPort() {
  for (let attempt = 0; attempt < MOCK_PORT_RANGE; attempt += 1) {
    const port = MOCK_PORT_MIN + ((MOCK_PORT_BASE - MOCK_PORT_MIN + nextMockPortOffset) % MOCK_PORT_RANGE);
    nextMockPortOffset += 1;
    if (USED_MOCK_PORTS.has(port) || !reserveMockPort(port)) continue;
    const available = await new Promise((resolve, reject) => {
      const probe = net.createServer();
      probe.unref();
      probe.once('error', (error) => {
        if (error && ['EADDRINUSE', 'EACCES', 'EPERM'].includes(error.code)) resolve(false);
        else reject(error);
      });
      probe.listen(port, '127.0.0.1', () => {
        probe.close((error) => {
          if (error) reject(error);
          else resolve(true);
        });
      });
    });
    if (!available) continue;
    USED_MOCK_PORTS.add(port);
    return port;
  }
  throw new Error('failed to allocate a unique loopback mock port');
}

async function mockHealth() {
  try {
    const response = await mockFetch(`${API}/health`);
    const body = await response.json();
    return response.ok && body.runtime === 'mock';
  } catch (_) {
    return false;
  }
}

async function waitForMockStopped() {
  const until = Date.now() + 10000;
  while (Date.now() < until) {
    if (!(await mockHealth())) return true;
    await wait(100);
  }
  return false;
}

async function stopMock(proc = null) {
  try {
    await mockFetch(`${API}/__test/shutdown`, { method: 'POST' });
  } catch (_) {}
  if (!(await waitForMockStopped()) && proc && proc.exitCode === null) proc.kill();
  if (proc && proc.exitCode === null) {
    await Promise.race([
      new Promise((resolve) => proc.once('exit', resolve)),
      wait(5000),
    ]);
    if (proc.exitCode === null) proc.kill('SIGKILL');
  }
}

async function startMock() {
  MOCK_PORT = FIXED_MOCK_PORT || await allocateMockPort();
  API = `http://127.0.0.1:${MOCK_PORT}`;
  try {
    const response = await mockFetch(`${API}/health`);
    if (response.ok) {
      const body = await response.json();
      if (body.runtime !== 'mock') throw new Error(`mock port ${MOCK_PORT} is already used by a non-mock API`);
      await stopMock();
    }
  } catch (error) {
    if (String(error.message || error).includes('non-mock')) throw error;
  }

  const proc = spawn(process.execPath, ['scripts/mock-voice-api.js'], {
    cwd: ROOT,
    env: {
      ...process.env,
      MOCK_VOICE_PORT: String(MOCK_PORT),
      MOCK_VOICE_TOKEN: MOCK_RUN_TOKEN,
    },
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
  const response = await mockFetch(`${API}/__test/events`);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.ok).toBe(true);
  return body.events;
}

async function controlSnapshot() {
  const response = await mockFetch(`${API}/v1/control-panel`);
  expect(response.status).toBe(200);
  return response.json();
}

async function updateControlSettings(payload) {
  const response = await mockFetch(`${API}/v1/control-panel/settings`, {
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
  const response = await mockFetch(`${API}/v1/control-panel/command`, {
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
  const response = await mockFetch(`${API}/v1/conversation/event`, {
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

async function waitForLiveChunks(count) {
  await expect.poll(async () => {
    const events = await apiEvents();
    return events.filter((event) => event.method === 'POST' && event.path === '/v1/live/chunks').length;
  }, { timeout: 30000 }).toBe(count);
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
  const options = {
    headless: process.env.PLAYWRIGHT_HEADED !== '1',
    viewport: { width: 1280, height: 720 },
    args: [
      `--disable-extensions-except=${EXTENSION}`,
      `--load-extension=${EXTENSION}`,
      '--autoplay-policy=no-user-gesture-required',
      '--no-first-run',
      '--mute-audio',
      ...(BROWSER_EXECUTABLE ? ['--window-position=-2400,0', '--disable-sync'] : []),
    ],
  };
  if (BROWSER_EXECUTABLE) options.executablePath = BROWSER_EXECUTABLE;
  else options.channel = 'chromium';
  return chromium.launchPersistentContext(PROFILE, options);
}

async function configureWorker(worker, values = {}) {
  await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('settingsVersion')).settingsVersion)).toBe(11);
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
        const copy = document.createElement('button');
        copy.dataset.testid = 'copy-turn-action-button';
        copy.setAttribute('aria-label', 'Copy');
        turn.append(copy);
        turn.append(reply);
        document.querySelector('#chat').append(turn);
      });
    </script>
  </body></html>`;
}

function proseMirrorMicrophoneFixtureHtml() {
  return `<!doctype html><html><head><meta charset="utf-8"><title>ChatGPT ProseMirror microphone fixture</title></head><body>
    <main id="chat"></main>
    <form id="prompt-form">
      <div id="prompt-textarea" class="ProseMirror" contenteditable="true"><p><br></p></div>
      <button type="button" data-testid="send-button" disabled>送信</button>
    </form>
    <script>
      window.__sent = [];
      window.__inputEvents = [];
      window.__delayedInputPulseCount = 0;
      window.__delayedInputPulse = null;
      window.__dispatchDelayedNativeInput = false;
      let composer = document.querySelector('#prompt-textarea');
      const send = document.querySelector('[data-testid="send-button"]');
      const attachComposer = (element) => {
        element.addEventListener('input', (event) => {
          const text = element.innerText.trim();
          window.__inputEvents.push({ trusted: event.isTrusted, inputType: event.inputType || '', text });
          if (event.isTrusted) send.disabled = !text;
          if (event.isTrusted && window.__dispatchDelayedNativeInput) {
            window.__dispatchDelayedNativeInput = false;
            setTimeout(() => {
              window.__delayedInputPulse = setInterval(() => {
                window.__delayedInputPulseCount += 1;
                element.dispatchEvent(new InputEvent('input', {
                  bubbles: true,
                  inputType: 'insertText',
                }));
              }, 2);
              setTimeout(() => {
                clearInterval(window.__delayedInputPulse);
                window.__delayedInputPulse = null;
              }, 250);
            }, 650);
          }
        });
      };
      attachComposer(composer);
      send.addEventListener('click', () => {
        const text = composer.innerText.trim();
        if (!text || send.disabled) return;
        if (window.__delayedInputPulse) {
          clearInterval(window.__delayedInputPulse);
          window.__delayedInputPulse = null;
        }
        window.__sent.push(text);
        const replacement = composer.cloneNode(false);
        replacement.innerHTML = '<p><br></p>';
        composer.replaceWith(replacement);
        composer = replacement;
        attachComposer(composer);
        composer.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'deleteContentBackward' }));
        const turn = document.createElement('article');
        turn.dataset.testid = 'conversation-turn-assistant-prosemirror';
        const reply = document.createElement('div');
        reply.dataset.messageAuthorRole = 'assistant';
        reply.dataset.messageId = 'prosemirror-reply-' + window.__sent.length;
        reply.textContent = 'ProseMirrorから送信された質問への返答です。';
        const copy = document.createElement('button');
        copy.dataset.testid = 'copy-turn-action-button';
        copy.setAttribute('aria-label', 'Copy');
        turn.append(copy);
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
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto excludes compact source chips even when their labels do not match host order', async () => {
  test.setTimeout(60000);
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
    await page.goto('https://chatgpt.com/c/source-chip', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const turn = document.createElement('article');
      turn.dataset.testid = 'conversation-turn-assistant-source-chip';
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'source-chip-reply';
      reply.innerHTML = [
        '<span>おにいちゃん、</span>',
        '<span class="source-chip">',
        '<a href="https://one.google.com/about/plans">Google One</a><span>+2</span>',
        '<a href="https://support.google.com/googleone/answer/9004013">Google ヘルプ</a>',
        '<a href="https://one.google.com/about/plans">Google One</a>',
        '</span>',
        '<span>Googleの個人向けAIサブスクで使えるサービスをまとめるね。</span>',
      ].join('');
      const copy = document.createElement('button');
      copy.dataset.testid = 'copy-turn-action-button';
      copy.setAttribute('aria-label', 'Copy');
      turn.append(copy, reply);
      document.querySelector('#chat').append(turn);
    });

    await waitForCounts(1, 1);
    const speak = (await apiEvents()).find((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(speak.body.text).toContain('おにいちゃん、Googleの個人向けAIサブスク');
    expect(speak.body.text).not.toContain('Google One');
    expect(speak.body.text).not.toContain('Google ヘルプ');
    expect(speak.body.text).not.toContain('+2');
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('inline code text is preserved before Auto finalizes a streaming preview', async () => {
  test.setTimeout(60000);
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
    await page.goto('https://chatgpt.com/c/inline-code-stream', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'inline-code-stream-reply';
      const block = document.createElement('pre');
      block.textContent = 'BLOCK-SAMPLE';
      reply.append(block, document.createTextNode('206は「'));
      document.querySelector('#chat').append(reply);
      window.setTimeout(() => {
        const code = document.createElement('code');
        code.textContent = 'Partial Content（部分コンテンツ）」で、動画を一部分だけ正常に返したという成功ステータスです。';
        reply.append(code);
      }, 250);
    });

    await waitForCounts(1, 1);
    const firstPost = (await apiEvents()).find((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(firstPost.body.text).toContain('Partial Content（部分コンテンツ）');
    expect(firstPost.body.text).not.toContain('BLOCK-SAMPLE');
    expect(firstPost.body.text).not.toBe('206は「');
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
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
      reply.textContent = '概ね妥当です。公開時の説明として成立していますが、追加で確認すべき項目があります。';
      document.querySelector('#chat').append(reply);
    });
    await waitForCounts(1, 1);

    await page.evaluate(() => {
      document.querySelector('[data-message-id="streaming-next-reply"]').textContent =
        '概ね妥当です。公開時の説明として成立していますが、追加で確認すべき項目があります。\nただし、公開時の誤認防止とブランド統一のために変更すべき項目があります。\nChrome拡張名、EXE名、スタートメニュー名、READMEタイトルを独自名称へ統一します。';
    });
    await page.waitForTimeout(700);

    await sendControlCommand('next');
    await waitForCounts(2, 2);
    const posts = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(posts[1].body.text).not.toBe('概ね妥当です。公開時の説明として成立していますが、追加で確認すべき項目があります。');
    expect(posts[1].body.text).toContain('ただし');
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto does not finalize a one-character streaming fragment and repairs the completed word', async () => {
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
    await page.goto('https://chatgpt.com/c/one-character-stream', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const stopButton = document.createElement('button');
      stopButton.dataset.testid = 'stop-button';
      stopButton.textContent = 'Stop generating';
      document.body.append(stopButton);
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'one-character-stream-reply';
      reply.textContent = '完';
      document.querySelector('#chat').append(reply);
    });
    await page.waitForTimeout(1800);
    expect((await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === '完')).toBe(false);

    await page.evaluate(() => {
      document.querySelector('[data-message-id="one-character-stream-reply"]').textContent =
        '完\n了状態 最初から再点検しました。';
      document.querySelector('[data-testid="stop-button"]').remove();
    });
    await expect.poll(async () => (await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === '完了状態 最初から再点検しました。'), { timeout: 30000 }).toBe(true);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto does not finalize a short unpunctuated streaming fragment before the reply grows', async () => {
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
    await page.goto('https://chatgpt.com/c/short-fragment-stream', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const stopButton = document.createElement('button');
      stopButton.dataset.testid = 'stop-button';
      stopButton.textContent = 'Stop generating';
      document.body.append(stopButton);
      const reply = document.createElement('div');
      reply.dataset.messageAuthorRole = 'assistant';
      reply.dataset.messageId = 'short-fragment-stream-reply';
      reply.textContent = 'ごめん';
      document.querySelector('#chat').append(reply);
    });
    await page.waitForTimeout(4200);
    expect((await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === 'ごめん')).toBe(false);

    await page.evaluate(() => {
      document.querySelector('[data-message-id="short-fragment-stream-reply"]').textContent =
        'ごめん、実際のCall of Dutyではなく、前に比較したブラウザゲームの話です。さっきの説明はズレていました。';
      document.querySelector('[data-testid="stop-button"]').remove();
    });
    await expect.poll(async () => (await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text.includes('実際のCall of Dutyではなく')), { timeout: 30000 }).toBe(true);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto ignores a short comma-ended partial until the response shows completion evidence', async () => {
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
    await page.goto('https://chatgpt.com/c/comma-ended-partial', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const turn = document.createElement('article');
      turn.dataset.testid = 'conversation-turn-assistant-comma-partial';
      const message = document.createElement('div');
      message.dataset.messageAuthorRole = 'assistant';
      message.dataset.messageId = 'comma-ended-partial-reply';
      message.textContent = 'いや、';
      turn.append(message);
      document.querySelector('#chat').append(turn);
    });
    await page.waitForTimeout(4200);
    expect((await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === 'いや、')).toBe(false);

    await page.evaluate(() => {
      const turn = document.querySelector('[data-testid="conversation-turn-assistant-comma-partial"]');
      turn.querySelector('[data-message-id="comma-ended-partial-reply"]').textContent = 'いや、残タスクはある。';
      const copy = document.createElement('button');
      copy.dataset.testid = 'copy-turn-action-button';
      copy.setAttribute('aria-label', 'Copy');
      turn.append(copy);
    });
    await expect.poll(async () => (await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === 'いや、残タスクはある。'), { timeout: 30000 }).toBe(true);
  } finally {
    await context.close().catch(() => {});
    if (api) api.kill();
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('Auto ignores repeated bare GitHub source labels while a short reply is still streaming', async () => {
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
    await page.goto('https://chatgpt.com/c/github-source-labels', { waitUntil: 'domcontentloaded' });
    await waitForControlReady(1);
    await updateControlSettings({ enabled: true, voiceVolume: 0, referenceVoice: 'sample' });
    await expect.poll(async () => worker.evaluate(async () => (await chrome.storage.local.get('enabled')).enabled)).toBe(true);

    await page.evaluate(() => {
      const stopButton = document.createElement('button');
      stopButton.dataset.testid = 'stop-button';
      stopButton.textContent = 'Stop generating';
      document.body.append(stopButton);

      const turn = document.createElement('article');
      turn.dataset.testid = 'conversation-turn-assistant-github-labels';
      const message = document.createElement('div');
      message.dataset.messageAuthorRole = 'assistant';
      message.dataset.messageId = 'github-labels-reply';
      const prose = document.createElement('span');
      prose.dataset.testid = 'reply-prose';
      prose.textContent = '見つ';
      message.append(prose);
      for (let index = 0; index < 8; index += 1) {
        const source = document.createElement('a');
        source.href = `https://github.com/example/repo-${index}`;
        source.textContent = 'GitHub';
        message.append(source);
      }
      turn.append(message);
      document.querySelector('#chat').append(turn);
    });

    await page.waitForTimeout(4200);
    expect((await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && String(event.body.text || '').includes('GitHubGitHub'))).toBe(false);

    await page.evaluate(() => {
      const turn = document.querySelector('[data-testid="conversation-turn-assistant-github-labels"]');
      turn.querySelector('[data-testid="reply-prose"]').textContent =
        '見つかったよ。公開リポジトリの候補を比較して、最も近いものを説明します。';
      document.querySelector('[data-testid="stop-button"]').remove();
      const copy = document.createElement('button');
      copy.dataset.testid = 'copy-turn-action-button';
      copy.setAttribute('aria-label', 'Copy');
      turn.append(copy);
    });

    await expect.poll(async () => (await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && event.body.text === '見つかったよ。公開リポジトリの候補を比較して、最も近いものを説明します。'), { timeout: 30000 }).toBe(true);
    expect((await apiEvents()).some((event) => event.method === 'POST'
      && event.path === '/v1/speak'
      && String(event.body.text || '').includes('GitHubGitHub'))).toBe(false);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
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
    await stopMock(api);
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

    const extensionId = new URL(worker.url()).host;
    const controllerPage = await context.newPage();
    await controllerPage.goto(`chrome-extension://${extensionId}/options.html`);
    const { backgroundTabId, foregroundTabId } = await controllerPage.evaluate(async () => {
      const [backgroundTab] = await chrome.tabs.query({ url: 'https://chatgpt.com/c/completion-marker-0' });
      const [foregroundTab] = await chrome.tabs.query({ url: 'https://chatgpt.com/c/completion-marker-1' });
      return {
        backgroundTabId: backgroundTab && backgroundTab.id,
        foregroundTabId: foregroundTab && foregroundTab.id,
      };
    });
    expect(backgroundTabId).toBeTruthy();
    expect(foregroundTabId).toBeTruthy();
    await controllerPage.evaluate(async (tabId) => {
      await chrome.tabs.update(tabId, { active: true });
    }, foregroundTabId);
    await expect.poll(async () => controllerPage.evaluate(async () => {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      return activeTab && activeTab.url;
    }), { timeout: 10000 }).toContain('/completion-marker-1');
    await controllerPage.evaluate(async (tabId) => {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const stopButton = document.createElement('button');
          stopButton.dataset.testid = 'stop-button';
          stopButton.textContent = 'Stop generating';
          document.body.append(stopButton);

          const turn = document.createElement('article');
          turn.dataset.testid = 'conversation-turn-assistant-completion-marker';
          const reply = document.createElement('div');
          reply.dataset.messageAuthorRole = 'assistant';
          reply.dataset.messageId = 'completion-marker-reply';
          reply.textContent = 'バックグラウンドタブで生成中の返答プレビューが読み上げられ、その後に完了状態へ移行します。';
          turn.append(reply);
          document.querySelector('#chat').append(turn);
        },
      });
    }, backgroundTabId);

    await waitForCounts(1, 1);
    await wait(1200);
    expect(await backgroundPage.title()).toBe('Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);

    await controllerPage.evaluate(async (tabId) => {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: () => document.querySelector('[data-testid="stop-button"]')?.remove(),
      });
    }, backgroundTabId);
    await expect.poll(() => backgroundPage.title(), { timeout: 10000 }).toBe('● Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveAttribute('href', /^data:image\/svg\+xml,/);
    expect(await foregroundPage.title()).toBe('Local Voice Demo Fixture');
    await expect(foregroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);

    await backgroundPage.bringToFront();
    await expect.poll(() => backgroundPage.title(), { timeout: 5000 }).toBe('Local Voice Demo Fixture');
    await expect(backgroundPage.locator('#local-voice-completion-favicon')).toHaveCount(0);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('microphone transcript supports Esc cancellation, 0.7 second auto-send, and Live reply chunks', async () => {
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
    await waitForLiveChunks(1);
    const liveEvents = (await apiEvents()).filter((event) => event.path === '/v1/live/chunks');
    expect(liveEvents[0].body.profile).toBe('speed');
    expect(liveEvents[0].body.isFinal).toBe(true);
    expect((await apiEvents()).filter((event) => event.path === '/v1/speak')).toHaveLength(0);

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
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});

test('microphone transcript commits and sends through a ProseMirror composer that is replaced on submit', async () => {
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
      body: proseMirrorMicrophoneFixtureHtml(),
    }));
    await page.goto('https://chatgpt.com/c/prosemirror-microphone', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#prompt-textarea')).toBeVisible();
    await waitForControlReady(1);
    await updateControlSettings({
      enabled: true,
      micConversationEnabled: true,
      voiceVolume: 0,
      referenceVoice: '',
      cancelGraceMs: 700,
    });

    await sendConversationEvent('transcript', {
      sessionId: 40,
      text: 'ProseMirrorでキャンセルする音声入力',
      cancelGraceMs: 1200,
    });
    await expect(page.locator('#prompt-textarea')).toContainText('ProseMirrorでキャンセルする音声入力');
    await expect(page.locator('[data-testid="send-button"]')).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(page.locator('#prompt-textarea')).toHaveText('');
    await expect(page.locator('[data-testid="send-button"]')).toBeDisabled();
    expect(await page.evaluate(() => window.__sent)).toEqual([]);

    await page.evaluate(() => { window.__dispatchDelayedNativeInput = true; });
    await sendConversationEvent('transcript', {
      sessionId: 41,
      text: 'ProseMirrorへ確実に送信する音声入力',
      cancelGraceMs: 700,
    });

    await expect(page.locator('#prompt-textarea')).toContainText('ProseMirrorへ確実に送信する音声入力');
    await page.waitForTimeout(100);
    const beforeSend = await page.evaluate(() => ({
      text: document.querySelector('#prompt-textarea')?.innerText || '',
      disabled: document.querySelector('[data-testid="send-button"]')?.disabled,
      events: window.__inputEvents,
    }));
    expect(beforeSend.text).toBe('ProseMirrorへ確実に送信する音声入力');
    expect(beforeSend.disabled).toBe(false);
    expect(beforeSend.events.some((event) => event.trusted && event.inputType === 'insertText')).toBe(true);
    await expect.poll(() => page.evaluate(() => window.__sent.length), { timeout: 5000 }).toBe(1);
    expect(await page.evaluate(() => window.__sent[0])).toBe('ProseMirrorへ確実に送信する音声入力');
    await expect(page.locator('[data-message-id="prosemirror-reply-1"]')).toBeVisible();
    await expect.poll(async () => (await apiEvents()).filter((event) => (
      event.path === '/v1/conversation/submission' && event.body && event.body.action === 'commit'
    )).length).toBe(1);
    const inputEvents = await page.evaluate(() => window.__inputEvents);
    expect(inputEvents.some((event) => !event.trusted && event.inputType === 'insertText')).toBe(true);
    expect(inputEvents.some((event) => event.trusted && event.text.includes('ProseMirror'))).toBe(true);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
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
      const copy = document.createElement('button');
      copy.dataset.testid = 'copy-turn-action-button';
      copy.setAttribute('aria-label', 'Copy');
      turn.append(copy);
      turn.append(message);
      document.body.append(turn);
    });

    await waitForCounts(1, 1);
    const posts = (await apiEvents()).filter((event) => event.method === 'POST' && event.path === '/v1/speak');
    expect(posts[0].body.text).toBe('はい、返事できます。');
    await expect(page.locator('#local-voice-bridge-panel')).toHaveCount(0);
  } finally {
    await context.close().catch(() => {});
    await stopMock(api);
    fs.rmSync(PROFILE, { recursive: true, force: true });
  }
});
