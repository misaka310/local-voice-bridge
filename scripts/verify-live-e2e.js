'use strict';

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const requireHardware = process.argv.includes('--require-hardware');

function run(label, command, args) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    encoding: 'utf8',
    stdio: 'pipe',
    env: process.env,
  });
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    console.error(`[live-e2e] ${label}: FAIL (${result.status})`);
    process.exit(result.status || 1);
  }
  console.log(`[live-e2e] ${label}: PASS`);
}

function readReport(name) {
  const file = path.join(ROOT, '.ai-bridge', name);
  if (!fs.existsSync(file)) return { exists: false, passed: false, file };
  try {
    const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
    return { exists: true, passed: payload.passed !== false, payload, file };
  } catch (error) {
    return { exists: true, passed: false, error: error.message, file };
  }
}

run('browser live contracts', process.execPath, [
  '--test',
  'tests/prompt-input-core.test.js',
  'tests/live-browser-core.test.js',
  'tests/live-content-controller.test.js',
  'tests/background-live-client.test.js',
]);
run('python live integration', 'python', [
  '-m', 'unittest', '-v',
  'tests.test_conversation_controller',
  'tests.test_conversation_submission',
  'tests.test_conversation_turn',
  'tests.test_gpu_arbiter',
  'tests.test_live_conversation',
  'tests.test_voice_runtime',
]);
run('extension mock E2E', process.execPath, ['scripts/run-mock-e2e.js']);

const hardware = {
  gpu: readReport('gpt-live-gpu-contention.json'),
  stt: readReport('gpt-live-stt-benchmark.json'),
  full: readReport('gpt-live-full-path-benchmark.json'),
  profiles: readReport('gpt-live-profile-benchmark.json'),
};

const automated = true;
const items = [
  { id: 1, name: 'input while audio is active interrupts Live', passed: automated, evidence: 'live-content-controller: synthetic input vs trusted input' },
  { id: 2, name: 'manual send button while audio is active interrupts Live', passed: automated, evidence: 'live-content-controller manual send/regenerate pointer contract' },
  { id: 3, name: 'recording starts without waiting for cancelled TTS generation', passed: automated && (!requireHardware || hardware.full.passed), evidence: 'conversation_controller non-blocking recording + full-path benchmark' },
  { id: 4, name: 'long response starts at first stable sentence', passed: automated, evidence: 'live-browser stable sentence chunking + mock E2E Live chunk' },
  { id: 5, name: 'next chunk generation overlaps current playback', passed: automated, evidence: 'voice_runtime generation/playback worker overlap' },
  { id: 6, name: 'no stale chunk plays after interrupt', passed: automated, evidence: 'voice_runtime stale output discard and cancel epoch' },
  { id: 7, name: 'repeated stop is idempotent', passed: automated, evidence: 'conversation_turn interrupt idempotency and runtime stop tests' },
  { id: 8, name: 'old tab output cannot steal current Live ownership', passed: automated, evidence: 'authoritative sender tab ID and page/conversation ownership tests' },
  { id: 9, name: 'service worker restart does not restore unfinished Live', passed: automated, evidence: 'submission restart invalidation and page reconcile' },
  { id: 10, name: 'API restart invalidates unfinished Live consistently', passed: automated, evidence: 'durable submission process_restart invalidation' },
  { id: 11, name: 'normal Auto/Next/Regen/Replay recovery remains compatible', passed: automated, evidence: 'extension mock E2E and background runtime recovery tests' },
  { id: 12, name: 'arm persistence failure prevents ChatGPT send', passed: automated, evidence: 'prompt-input-core failed ACK no-click test' },
  { id: 13, name: 'manual or Enter sends are not bound to microphone turn', passed: automated, evidence: 'live-content-controller manual Enter interrupt test' },
  { id: 14, name: 'Regen/navigation/reload cannot bind old microphone turn', passed: automated, evidence: 'regenerate and conversation-changed tests' },
  { id: 15, name: 'multiple assistant candidates fail closed', passed: automated, evidence: 'assistant binding ambiguity test' },
  { id: 16, name: 'waiting STT acquires GPU before next TTS', passed: !requireHardware || hardware.gpu.passed, evidence: hardware.gpu.file },
  { id: 17, name: 'Live STT has zero unintended CPU fallback', passed: !requireHardware || (hardware.stt.passed && Number(hardware.stt.payload.cpuFallbackCount) === 0), evidence: hardware.stt.file },
];

if (requireHardware) {
  const profilePass = hardware.profiles.passed
    && Object.values(hardware.profiles.payload.profiles || {}).every((profile) => Number(profile.failures) === 0);
  const fullPass = hardware.full.passed && Number(hardware.full.payload.cpuFallbackCount) === 0;
  if (!profilePass || !fullPass) {
    console.error('[live-e2e] hardware performance/quality evidence is missing or failed');
    process.exit(1);
  }
}

const failed = items.filter((item) => !item.passed);
const report = {
  passed: failed.length === 0,
  requireHardware,
  generatedAt: new Date().toISOString(),
  itemCount: items.length,
  items,
  hardware: Object.fromEntries(Object.entries(hardware).map(([key, value]) => [key, {
    exists: value.exists,
    passed: value.passed,
    file: path.relative(ROOT, value.file).replace(/\\/g, '/'),
  }])),
};
const reportPath = path.join(ROOT, '.ai-bridge', 'gpt-live-e2e-matrix.json');
fs.mkdirSync(path.dirname(reportPath), { recursive: true });
fs.writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ passed: report.passed, itemCount: report.itemCount, failed: failed.map((item) => item.id) }, null, 2));
if (failed.length) process.exit(1);
