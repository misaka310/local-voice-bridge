#!/usr/bin/env node
'use strict';

// Deterministic local substitute for CI and the browser demo. It never loads
// Python, CUDA, a model, a real audio device, or a reference voice.
const crypto = require('crypto');
const http = require('http');

const host = '127.0.0.1';
const port = Number(process.env.MOCK_VOICE_PORT || 8717);
const requiredTestToken = String(process.env.MOCK_VOICE_TOKEN || '');
const events = [];
const referenceVoices = [{ id: '', label: 'none' }, { id: 'sample', label: 'sample' }];
let control;

function emptyBrowserRuntime() {
  return {
    tabs: [],
    selectedTabId: 0,
    uiOwnerTabId: 0,
    queue: [],
    currentItem: null,
    lastPlayedItem: null,
    seq: 1,
  };
}

function resetControl() {
  control = {
    initialized: false,
    settingsRevision: 0,
    settings: {
      enabled: false,
      voiceVolume: 0.6,
      referenceVoice: '',
      referenceVoiceExplicit: false,
      micConversationEnabled: false,
      sttModel: 'small',
      cancelGraceMs: 700,
      liveTtsProfile: 'speed',
    },
    commands: [],
    consumerAcks: {},
    nextCommandId: 1,
    conversationEvents: [],
    nextConversationEventId: 1,
    browserRuntime: emptyBrowserRuntime(),
    conversation: {
      phase: 'off',
      statusText: 'マイク会話オフ',
      sttDevice: '',
      sttModel: 'small',
      error: '',
    },
    live: {
      submission: { phase: 'idle', current: {} },
      pendingChunks: 0,
      capacity: 2,
      maxPendingChunks: 2,
      cancelEpoch: 0,
      chunks: [],
    },
    extension: {
      connected: false,
      statusText: 'Waiting for ChatGPT',
      statusLevel: 'info',
      currentText: '',
      queueSize: 0,
      isPlaying: false,
      playbackPhase: 'idle',
      replayAvailable: false,
      tabsCount: 0,
      loadedVersion: '',
    },
  };
}
resetControl();

// A short valid PCM WAV retained for compatibility tests of an older browser
// playback client. The current bridge reports local playback completion without
// asking a ChatGPT tab to fetch this file.
const wav = Buffer.alloc(44 + 8000);
wav.write('RIFF', 0);
wav.writeUInt32LE(wav.length - 8, 4);
wav.write('WAVEfmt ', 8);
wav.writeUInt32LE(16, 16);
wav.writeUInt16LE(1, 20);
wav.writeUInt16LE(1, 22);
wav.writeUInt32LE(8000, 24);
wav.writeUInt32LE(8000, 28);
wav.writeUInt16LE(1, 32);
wav.writeUInt16LE(8, 34);
wav.write('data', 36);
wav.writeUInt32LE(8000, 40);
wav.fill(128, 44);

function json(res, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': body.length,
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function readJson(req, res, callback) {
  let raw = '';
  req.setEncoding('utf8');
  req.on('data', (chunk) => { raw += chunk; });
  req.on('end', () => {
    let body = {};
    try {
      body = JSON.parse(raw || '{}');
    } catch (_) {
      json(res, 400, { ok: false, error: 'invalid JSON' });
      return;
    }
    callback(body);
  });
}

function normalizeConsumerId(value) {
  const normalized = String(value || '')
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[._-]+|[._-]+$/g, '')
    .slice(0, 64);
  return normalized || 'legacy';
}

function consumerCursor(value, replayExisting) {
  const id = normalizeConsumerId(value);
  if (!control.consumerAcks[id]) {
    control.consumerAcks[id] = {
      command: replayExisting || id === 'legacy' ? 0 : control.nextCommandId - 1,
      conversationEvent: replayExisting || id === 'legacy' ? 0 : control.nextConversationEventId - 1,
    };
  }
  return { id, cursor: control.consumerAcks[id] };
}

function compactAcknowledged() {
  const cursors = Object.values(control.consumerAcks);
  if (!cursors.length) return;
  const commandFloor = Math.min(...cursors.map((item) => Number(item.command || 0)));
  const eventFloor = Math.min(...cursors.map((item) => Number(item.conversationEvent || 0)));
  control.commands = control.commands.filter((item) => item.id > commandFloor);
  control.conversationEvents = control.conversationEvents.filter((item) => item.id > eventFloor);
}

function controlSnapshot(extra = {}) {
  return {
    ok: true,
    initialized: control.initialized,
    settingsRevision: control.settingsRevision,
    settings: { ...control.settings },
    extension: { ...control.extension },
    conversation: { ...control.conversation },
    lastCommandId: control.nextCommandId - 1,
    lastConversationEventId: control.nextConversationEventId - 1,
    referenceVoices,
    voiceRuntime: {
      readiness: 'ready',
      ready: true,
      detail: { runtime: 'mock' },
      error: '',
      repairRequired: false,
      dependencies: { sounddevice: true, soundfile: true },
      phase: control.extension.playbackPhase || 'idle',
      queueSize: control.extension.queueSize || 0,
      currentText: control.extension.currentText || '',
      lastOperation: '',
      replayAvailable: control.extension.replayAvailable,
      startedAt: 1,
    },
    readiness: {
      process: 'ready',
      dependencies: 'ready',
      browserExtension: control.extension.connected ? 'ready' : 'waiting',
      tabs: control.extension.tabsCount,
      deviceOrModel: 'ready',
      lastOperation: '',
      repairRequired: false,
      ready: Boolean(control.extension.connected && control.extension.tabsCount > 0),
    },
    ...extra,
  };
}

function record(method, pathname, body = undefined) {
  events.push({ method, path: pathname, body, responseStatus: 200, at: Date.now() });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${host}:${port}`);
  const suppliedTestToken = String(req.headers['x-local-voice-test-token'] || '');
  if (requiredTestToken && suppliedTestToken !== requiredTestToken) {
    return json(res, 403, { ok: false, error: 'wrong test run token' });
  }

  if (req.method === 'GET' && url.pathname === '/health') {
    return json(res, 200, {
      ok: true,
      engine: 'mock',
      runtime: 'mock',
      referenceVoices,
      voiceRuntime: controlSnapshot().voiceRuntime,
      readiness: controlSnapshot().readiness,
    });
  }
  if (req.method === 'GET' && url.pathname === '/v1/reference-voices') {
    return json(res, 200, { ok: true, voices: referenceVoices });
  }
  if (req.method === 'GET' && url.pathname === '/v1/control-panel') {
    return json(res, 200, controlSnapshot());
  }
  if (req.method === 'GET' && url.pathname === '/v1/browser-runtime') {
    return json(res, 200, { ok: true, browserRuntime: { ...control.browserRuntime } });
  }
  if (req.method === 'GET' && url.pathname === '/v1/live/state') {
    return json(res, 200, {
      ok: true,
      submission: { ...control.live.submission, current: { ...(control.live.submission.current || {}) } },
      conversationPhase: control.live.submission.phase === 'bound' ? 'responding' : 'idle',
      generationPhase: 'idle',
      playbackPhase: 'idle',
      turnId: String((control.live.submission.current || {}).turnId || ''),
      cancelEpoch: control.live.cancelEpoch,
      pendingChunks: control.live.pendingChunks,
      capacity: control.live.capacity,
      maxPendingChunks: control.live.maxPendingChunks,
      lastError: '',
    });
  }
  if (req.method === 'GET' && url.pathname === '/v1/control-panel/poll') {
    const after = Number(url.searchParams.get('after') || 0);
    const afterEvent = Number(url.searchParams.get('afterEvent') || 0);
    const replayExisting = url.searchParams.get('replayExisting') === '1';
    const consumer = consumerCursor(url.searchParams.get('consumer'), replayExisting);
    const commands = control.commands.filter(
      (item) => item.id > Math.max(after, Number(consumer.cursor.command || 0)),
    );
    const conversationEvents = control.conversationEvents.filter(
      (item) => item.id > Math.max(afterEvent, Number(consumer.cursor.conversationEvent || 0)),
    );
    return json(res, 200, controlSnapshot({ commands, conversationEvents }));
  }
  if (req.method === 'GET' && url.pathname === '/audio/mock.wav') {
    record('GET', url.pathname);
    res.writeHead(200, {
      'Content-Type': 'audio/wav',
      'Content-Length': wav.length,
      'Cache-Control': 'no-store',
    });
    res.end(wav);
    return;
  }
  if (req.method === 'GET' && url.pathname === '/__test/events') {
    return json(res, 200, { ok: true, events });
  }

  if (req.method === 'POST' && url.pathname === '/__test/reset') {
    events.length = 0;
    resetControl();
    return json(res, 200, { ok: true });
  }
  if (req.method === 'POST' && url.pathname === '/__test/shutdown') {
    json(res, 200, { ok: true, stopping: true });
    setImmediate(() => server.close(() => process.exit(0)));
    return;
  }
  if (req.method === 'POST' && url.pathname === '/v1/control-panel/settings') {
    return readJson(req, res, (body) => {
      if (Object.prototype.hasOwnProperty.call(body, 'enabled')) control.settings.enabled = Boolean(body.enabled);
      if (Object.prototype.hasOwnProperty.call(body, 'voiceVolume')) {
        control.settings.voiceVolume = Math.min(1, Math.max(0, Number(body.voiceVolume) || 0));
      }
      if (Object.prototype.hasOwnProperty.call(body, 'referenceVoice')
        || Object.prototype.hasOwnProperty.call(body, 'voiceId')) {
        control.settings.referenceVoice = String(body.referenceVoice ?? body.voiceId ?? '').trim();
        control.settings.referenceVoiceExplicit = Object.prototype.hasOwnProperty.call(body, 'referenceVoiceExplicit')
          ? Boolean(body.referenceVoiceExplicit)
          : true;
      } else if (Object.prototype.hasOwnProperty.call(body, 'referenceVoiceExplicit')) {
        control.settings.referenceVoiceExplicit = Boolean(body.referenceVoiceExplicit);
      }
      if (Object.prototype.hasOwnProperty.call(body, 'micConversationEnabled')) {
        control.settings.micConversationEnabled = Boolean(body.micConversationEnabled);
      }
      if (Object.prototype.hasOwnProperty.call(body, 'sttModel')) {
        const model = String(body.sttModel || 'small').trim();
        control.settings.sttModel = ['small', 'medium', 'large-v3-turbo'].includes(model) ? model : 'small';
      }
      if (Object.prototype.hasOwnProperty.call(body, 'cancelGraceMs')) {
        control.settings.cancelGraceMs = Math.max(0, Math.min(5000, Math.round(Number(body.cancelGraceMs) || 0)));
      }
      if (Object.prototype.hasOwnProperty.call(body, 'liveTtsProfile')) {
        const profile = String(body.liveTtsProfile || '').trim().toLowerCase();
        control.settings.liveTtsProfile = ['speed', 'balanced', 'bridge'].includes(profile) ? profile : 'speed';
      }
      control.initialized = true;
      control.settingsRevision += 1;
      record('POST', url.pathname, body);
      return json(res, 200, controlSnapshot());
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/control-panel/command') {
    return readJson(req, res, (body) => {
      const command = String(body.command || '').trim().toLowerCase();
      if (!['next', 'regen', 'replay', 'stop'].includes(command)) {
        return json(res, 400, { ok: false, error: 'unsupported command' });
      }
      const item = { id: control.nextCommandId++, command, createdAt: Date.now() / 1000 };
      control.commands.push(item);
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, command: item });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/control-panel/ack') {
    return readJson(req, res, (body) => {
      const hasCommand = Object.prototype.hasOwnProperty.call(body, 'commandId');
      const hasEvent = Object.prototype.hasOwnProperty.call(body, 'conversationEventId');
      if (!hasCommand && !hasEvent) {
        return json(res, 400, { ok: false, error: 'commandId or conversationEventId is required' });
      }
      const consumer = consumerCursor(body.consumerId, true);
      const result = { ok: true, consumerId: consumer.id };
      if (hasCommand) {
        consumer.cursor.command = Math.max(Number(consumer.cursor.command || 0), Number(body.commandId || 0));
        result.commandId = consumer.cursor.command;
      }
      if (hasEvent) {
        consumer.cursor.conversationEvent = Math.max(
          Number(consumer.cursor.conversationEvent || 0),
          Number(body.conversationEventId || 0),
        );
        result.conversationEventId = consumer.cursor.conversationEvent;
      }
      compactAcknowledged();
      record('POST', url.pathname, body);
      return json(res, 200, result);
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/browser-runtime') {
    return readJson(req, res, (body) => {
      control.browserRuntime = { ...emptyBrowserRuntime(), ...body };
      return json(res, 200, { ok: true, browserRuntime: control.browserRuntime });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/control-panel/state') {
    return readJson(req, res, (body) => {
      control.extension = { ...control.extension, ...body, connected: true };
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, extension: control.extension });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/conversation/state') {
    return readJson(req, res, (body) => {
      control.conversation = {
        ...control.conversation,
        phase: String(body.phase || 'error'),
        statusText: String(body.statusText || ''),
        sttDevice: String(body.sttDevice || ''),
        sttModel: String(body.sttModel || control.settings.sttModel || 'small'),
        error: String(body.error || ''),
      };
      record('POST', url.pathname, control.conversation);
      return json(res, 200, { ok: true, conversation: control.conversation });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/conversation/event') {
    return readJson(req, res, (body) => {
      const type = String(body.type || '').trim();
      const payload = body.payload && typeof body.payload === 'object' ? body.payload : {};
      if (!['cancel_pending', 'transcript'].includes(type)) {
        return json(res, 400, { ok: false, error: 'unsupported conversation event' });
      }
      if (type === 'transcript' && !String(payload.text || '').trim()) {
        return json(res, 400, { ok: false, error: 'transcript text is required' });
      }
      const safePayload = type === 'transcript'
        ? { ...payload, deliveryId: String(payload.deliveryId || crypto.randomUUID()) }
        : { ...payload };
      const item = {
        id: control.nextConversationEventId++,
        type,
        payload: safePayload,
        createdAt: Date.now() / 1000,
      };
      control.conversationEvents.push(item);
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, event: item });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/conversation/submission') {
    return readJson(req, res, (body) => {
      const action = String(body.action || '').trim().toLowerCase();
      const current = control.live.submission.current || {};
      if (action === 'arm') {
        control.live.cancelEpoch = Math.max(0, Number(body.cancelEpoch) || 0);
        control.live.submission = {
          phase: 'armed',
          current: { ...body, phase: 'armed' },
        };
      } else {
        if (!current.submissionId || String(current.submissionId) !== String(body.submissionId || '')) {
          return json(res, 409, { ok: false, error: 'submission identity conflict' });
        }
        if (action === 'commit') {
          control.live.submission = { phase: 'committed', current: { ...current, phase: 'committed' } };
        } else if (action === 'bind') {
          if (Number(body.candidateCount) !== 1) {
            return json(res, 409, { ok: false, error: 'assistant reply binding is ambiguous' });
          }
          control.live.submission = {
            phase: 'bound',
            current: { ...current, phase: 'bound', assistantMessageKey: String(body.assistantMessageKey || '') },
          };
        } else if (action === 'invalidate') {
          control.live.submission = {
            phase: 'invalidated',
            current: { ...current, phase: 'invalidated', invalidatedReason: String(body.reason || 'invalidated') },
          };
        } else if (action === 'complete') {
          control.live.submission = { phase: 'completed', current: { ...current, phase: 'completed' } };
        } else {
          return json(res, 400, { ok: false, error: 'unsupported submission action' });
        }
      }
      record('POST', url.pathname, body);
      return json(res, 200, {
        ok: true,
        action,
        submission: { ...(control.live.submission.current || {}) },
        sendAllowed: action === 'arm' && control.live.submission.phase === 'armed',
      });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/live/chunks') {
    return readJson(req, res, (body) => {
      const current = control.live.submission.current || {};
      if (control.live.submission.phase !== 'bound'
        || String(current.submissionId || '') !== String(body.submissionId || '')
        || String(current.assistantMessageKey || '') !== String(body.assistantMessageKey || '')) {
        return json(res, 409, { ok: false, error: 'microphone submission is not bound' });
      }
      const key = `${body.generationId}:${body.chunkIndex}:${body.textHash}`;
      const existing = control.live.chunks.find((item) => item.key === key);
      if (existing) {
        if (body.isFinal) existing.isFinal = true;
        if (existing.isFinal) {
          control.live.submission = { phase: 'completed', current: { ...current, phase: 'completed' } };
        }
        record('POST', url.pathname, body);
        return json(res, 202, {
          ok: true,
          accepted: true,
          duplicate: true,
          generationId: body.generationId,
          chunkIndex: Number(body.chunkIndex),
          pendingChunks: 0,
          capacity: 2,
        });
      }
      control.live.chunks.push({ key, ...body });
      if (body.isFinal) {
        control.live.submission = { phase: 'completed', current: { ...current, phase: 'completed' } };
      }
      record('POST', url.pathname, body);
      return json(res, 202, {
        ok: true,
        accepted: true,
        duplicate: false,
        generationId: body.generationId,
        chunkIndex: Number(body.chunkIndex),
        pendingChunks: 0,
        capacity: 2,
        profile: String(body.profile || 'speed'),
      });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/interrupt') {
    return readJson(req, res, (body) => {
      const current = control.live.submission.current || {};
      control.live.cancelEpoch = Math.max(control.live.cancelEpoch, Number(body.cancelEpoch || 0) + 1);
      control.live.submission = {
        phase: 'invalidated',
        current: { ...current, phase: 'invalidated', invalidatedReason: String(body.reason || 'interrupt') },
      };
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, stopping: true, cancelEpoch: control.live.cancelEpoch });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/desktop-pet') {
    return readJson(req, res, (body) => {
      const selectedPetId = String(body.petId || 'placeholder').trim() || 'placeholder';
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, selectedPetId, visible: true });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/playback/stop') {
    return readJson(req, res, (body) => {
      record('POST', url.pathname, body);
      return json(res, 200, { ok: true, stopping: true });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/playback/replay') {
    return readJson(req, res, (body) => {
      record('POST', url.pathname, body);
      return json(res, 200, {
        ok: true,
        audioUrl: `http://${host}:${port}/audio/mock.wav`,
        playedLocally: true,
        playbackCompleted: true,
        stopped: false,
      });
    });
  }
  if (req.method === 'POST' && url.pathname === '/v1/speak') {
    return readJson(req, res, (body) => {
      const referenceVoice = String(body.voiceId || body.referenceVoice || '').trim();
      record('POST', url.pathname, body);
      return json(res, 200, {
        ok: true,
        engine: 'mock',
        runtime: 'mock',
        model: 'irodori-v3',
        voiceId: referenceVoice,
        voiceProfile: 'irodori-v3',
        referenceVoice,
        usedReferenceAudio: referenceVoice ? 'mock-reference.wav' : '',
        audioUrl: `http://${host}:${port}/audio/mock.wav`,
        playedLocally: Boolean(body.playLocal),
        playbackCompleted: Boolean(body.playLocal),
        stopped: false,
      });
    });
  }

  return json(res, 404, { ok: false, error: 'not found' });
});

server.listen(port, host, () => console.log(`Mock Voice API listening on http://${host}:${port}`));
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
