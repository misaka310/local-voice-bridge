#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const failures = [];

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8').replace(/\r\n/g, '\n');
}

function requireFile(relativePath) {
  if (!fs.existsSync(path.join(ROOT, relativePath))) {
    failures.push(`${relativePath}: required architecture module is missing`);
  }
}

function requireText(relativePath, expected, reason) {
  const text = read(relativePath);
  if (!text.includes(expected)) {
    failures.push(`${relativePath}: ${reason}`);
  }
}

function forbidText(relativePath, forbidden, reason) {
  const text = read(relativePath);
  if (text.includes(forbidden)) {
    failures.push(`${relativePath}: ${reason}`);
  }
}

function capLines(relativePath, maximum) {
  const count = read(relativePath).split(/\r?\n/).length;
  if (count > maximum) {
    failures.push(
      `${relativePath}: ${count} lines exceeds architecture cap ${maximum}; extract a focused module instead of growing the orchestrator`,
    );
  }
}

for (const file of [
  'local-api/state_normalization.py',
  'local-api/browser_runtime_state.py',
  'local-api/durable_outbox.py',
  'local-api/http_io.py',
  'local-api/api_router.py',
  'local-api/control_panel_client.py',
  'local-api/panel_window_state.py',
  'local-api/audio_recorder.py',
  'local-api/stt_runtime.py',
  'local-api/windows_push_to_talk.py',
  'local-api/dictation_pause_notifier.py',
  'local-api/runtime_readiness.py',
  'local-api/installation_identity.py',
  'local-api/gpu_arbiter.py',
  'local-api/tts_profiles.py',
  'local-api/audio_quality.py',
  'local-api/runtime_events.py',
  'local-api/conversation_submission.py',
  'local-api/live_conversation.py',
  'local-api/live_http.py',
  'local-api/conversation_turn.py',
  'local-api/voice_job_queue.py',
  'local-api/voice_runtime.py',
  'local-api/voice_service.py',
  'extension/background-core.js',
  'extension/background-settings-core.js',
  'extension/background-runtime-core.js',
  'extension/background-queue-core.js',
  'extension/background-control-sync.js',
  'extension/background-tab-registry.js',
  'extension/background-conversation-target.js',
  'extension/background-local-api-client.js',
  'extension/background-runtime-store.js',
  'extension/background-playback-queue.js',
  'extension/background-message-router.js',
  'extension/background-live-client.js',
  'extension/live-browser-core.js',
  'extension/live-content-controller.js',
  'extension/prompt-input-core.js',
  'extension/assistant-source-filter.js',
  'extension/assistant-text-extractor.js',
  'extension/auto-speech-controller.js',
  'extension/content-settings.js',
  'extension/content-dom-observer.js',
  'extension/content-completion-marker.js',
  'extension/content-audio-player.js',
  'extension/content-conversation-bridge.js',
  'extension/content-message-router.js',
  'tests/e2e/assistant-text-extractor-dom.spec.js',
]) {
  requireFile(file);
}

capLines('local-api/control_state.py', 350);
capLines('local-api/durable_outbox.py', 420);
capLines('local-api/server.py', 450);
capLines('local-api/api_router.py', 500);
capLines('local-api/conversation_controller.py', 520);
capLines('local-api/control_panel.py', 540);
capLines('extension/content.js', 330);
capLines('extension/background.js', 470);
capLines('extension/background-playback-queue.js', 330);
capLines('extension/background-message-router.js', 180);
capLines('extension/content-dom-observer.js', 260);
capLines('extension/content-audio-player.js', 240);
capLines('extension/content-conversation-bridge.js', 260);
capLines('extension/content-message-router.js', 80);
capLines('extension/assistant-source-filter.js', 200);
capLines('extension/assistant-text-extractor.js', 150);
capLines('extension/auto-speech-controller.js', 330);
capLines('extension/background-settings-core.js', 200);
capLines('extension/background-runtime-core.js', 190);
capLines('extension/background-queue-core.js', 260);
capLines('extension/background-control-sync.js', 330);
capLines('extension/background-live-client.js', 120);
capLines('extension/live-browser-core.js', 250);
capLines('extension/live-content-controller.js', 420);
capLines('extension/prompt-input-core.js', 520);

requireText(
  'CONTRIBUTING.md',
  '## Architecture boundaries',
  'contributors must receive the public architecture and verification contract',
);
requireText(
  'local-api/control_state.py',
  'self._outbox = DurableOutbox()',
  'ControlStateStore must coordinate the durable outbox instead of reimplementing ACK state',
);
requireText(
  'local-api/server.py',
  'from http_io import ResponseWriteError, is_normal_client_disconnect, json_response, request_json',
  'HTTP request/response handling must stay in http_io.py',
);
requireText(
  'local-api/server.py',
  'from runtime_readiness import enrich_snapshot, runtime_snapshot, structured_readiness',
  'readiness composition must stay in runtime_readiness.py',
);
requireText(
  'extension/background-entry.js',
  "'background-settings-core.js'",
  'the service worker must load tested settings rules before background.js',
);
requireText(
  'extension/background-entry.js',
  "'background-runtime-core.js'",
  'the service worker must load pure runtime-state helpers before background.js',
);
requireText(
  'extension/background-entry.js',
  "'background-queue-core.js'",
  'the service worker must load pure queue rules before background.js',
);
requireText(
  'extension/background-entry.js',
  "'background-control-sync.js'",
  'the service worker must load durable control synchronization before background.js',
);
requireText(
  'extension/manifest.json',
  '"content-settings.js", "content-mutation-filter.js", "content-dom-observer.js", "content-completion-marker.js", "content-conversation-bridge.js", "content-audio-player.js", "content-message-router.js", "content.js"',
  'content scripts must load focused controllers before content.js',
);
requireText(
  'extension/background-runtime-store.js',
  'ctx.runtimeCore.mergeSnapshot',
  'browser runtime restoration must use the tested pure runtime-state module',
);
requireText(
  'extension/background-playback-queue.js',
  'ctx.queueCore.planManualCommand',
  'Next and Regen planning must use background-queue-core.js',
);
requireText(
  'extension/background.js',
  'BackgroundControlSync.create',
  'external poll, ACK, and transcript routing must use background-control-sync.js',
);
requireText(
  'extension/content-conversation-bridge.js',
  'global.LocalVoicePromptInput',
  'ChatGPT Composer operations must use prompt-input-core.js',
);
requireText(
  'extension/content-dom-observer.js',
  'autoSpeech.createAutoSpeechController',
  'Auto response lifecycle must use auto-speech-controller.js',
);
requireText(
  'extension/content-dom-observer.js',
  'assistantText.extractAssistantText',
  'assistant DOM extraction must use assistant-text-extractor.js',
);

forbidText(
  'local-api/control_state.py',
  '_SAFE_CONSUMER_ID =',
  'consumer/ACK normalization belongs in durable_outbox.py',
);
requireText(
  'local-api/durable_outbox.py',
  'CONSUMER_ACK_LIMIT = 32',
  'durable consumer ACK state must remain bounded',
);
requireText(
  'local-api/durable_outbox.py',
  'CONSUMER_ACK_TTL_SECONDS = 7 * 24 * 60 * 60',
  'inactive durable consumers must expire instead of blocking outbox compaction forever',
);
forbidText(
  'local-api/control_state.py',
  'def _normalize_browser_runtime',
  'browser runtime schemas belong in browser_runtime_state.py',
);
forbidText(
  'local-api/server.py',
  '_CLIENT_DISCONNECT_ERRNOS =',
  'socket write handling belongs in http_io.py',
);
forbidText(
  'local-api/server.py',
  'def structured_readiness(',
  'readiness composition belongs in runtime_readiness.py',
);
forbidText(
  'local-api/server.py',
  'if parsed.path ==',
  'individual HTTP routes belong in api_router.py',
);
forbidText(
  'local-api/conversation_controller.py',
  'import ctypes',
  'Windows keyboard hooks belong in windows_push_to_talk.py',
);
forbidText(
  'local-api/conversation_controller.py',
  'import sounddevice',
  'audio capture belongs in audio_recorder.py',
);
forbidText(
  'local-api/conversation_controller.py',
  'faster_whisper',
  'STT model loading belongs in stt_runtime.py',
);
forbidText(
  'local-api/control_panel.py',
  'urllib.request',
  'control-panel HTTP calls belong in control_panel_client.py',
);
forbidText(
  'extension/background.js',
  'function queueIdentity(',
  'queue identity and merge rules belong in background-runtime-core.js',
);
forbidText(
  'extension/background.js',
  'function sanitizeSettings(',
  'settings migration and normalization belong in background-settings-core.js',
);
forbidText(
  'extension/background.js',
  'let lastExternalCommandId',
  'external delivery cursors belong in background-control-sync.js',
);
forbidText(
  'extension/background.js',
  'async function getBridgeConsumerId(',
  'stable consumer identity belongs in background-control-sync.js',
);
forbidText(
  'extension/background.js',
  'async function applyExternalSettings(',
  'external settings synchronization belongs in background-control-sync.js',
);
forbidText(
  'extension/background.js',
  'function preserveReadChunkBoundary(',
  'streaming read boundaries belong in background-queue-core.js',
);
forbidText(
  'extension/background.js',
  'function selectedTarget(',
  'queue target selection belongs in background-queue-core.js',
);
forbidText(
  'extension/background.js',
  'fetch(',
  'loopback HTTP calls belong in background-local-api-client.js',
);
forbidText(
  'extension/background.js',
  'chrome.runtime.onMessage.addListener((',
  'runtime message dispatch belongs in background-message-router.js',
);
forbidText(
  'extension/content.js',
  'new Audio(',
  'audio element execution belongs in content-audio-player.js',
);
forbidText(
  'extension/content.js',
  'RESPONSE_GENERATING_SELECTOR',
  'ChatGPT response selectors belong in content-dom-observer.js',
);
forbidText(
  'extension/content.js',
  'createPendingSendController',
  'conversation delivery state belongs in content-conversation-bridge.js',
);
forbidText(
  'extension/content.js',
  'function removeDecorativeSourceLinks(',
  'assistant DOM cleanup belongs in assistant-text-extractor.js',
);
forbidText(
  'extension/content.js',
  'function shouldSendNow(',
  'Auto stability and completion decisions belong in auto-speech-controller.js',
);
forbidText(
  'extension/content.js',
  'new WeakMap()',
  'per-message Auto lifecycle state belongs in auto-speech-controller.js',
);
forbidText(
  'extension/content.js',
  'execCommand(',
  'native Composer editing belongs in prompt-input-core.js',
);

requireText(
  'scripts/playwright-mock.config.js',
  "'assistant-text-extractor-dom.spec.js'",
  'the source-chip DOM matrix must remain part of the normal mock browser gate',
);

requireText(
  'extension/assistant-text-extractor.js',
  'sourceFilter.removeDecorativeSourceLinks',
  'assistant source and citation filtering must use assistant-source-filter.js',
);
requireText(
  'extension/background-tab-reconnect.js',
  "'assistant-source-filter.js',\n    'assistant-text-extractor.js'",
  'reconnect injection must load the source filter before the assistant extractor',
);
requireText(
  'extension/background-tab-reconnect.js',
  "'content-settings.js',\n    'content-mutation-filter.js',\n    'content-dom-observer.js'",
  'reconnect injection must load the mutation filter before the DOM observer',
);
forbidText(
  'extension/assistant-text-extractor.js',
  'function sourceHint(',
  'source and citation DOM heuristics belong in assistant-source-filter.js',
);
forbidText(
  'extension/assistant-text-extractor.js',
  'function hasCompactSourceContext(',
  'source container classification belongs in assistant-source-filter.js',
);

if (failures.length) {
  console.error('ARCHITECTURE CHECK: FAIL');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log('ARCHITECTURE CHECK: PASS');
console.log('- durable state/outbox/browser schemas are separated');
console.log('- assistant text, Auto lifecycle, and queue rules are isolated from browser coordinators');
console.log('- HTTP I/O and voice runtime remain focused modules');
console.log('- orchestrator growth caps are enforced');
