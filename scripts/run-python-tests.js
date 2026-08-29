#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const TEST_MODULES = [
  'tests.test_server_loopback',
  'tests.test_control_state',
  'tests.test_control_panel',
  'tests.test_ux_responsibility_finalization',
  'tests.test_repair_signal_wiring',
  'tests.test_conversation_controller',
  'tests.test_preflight_versions',
  'tests.test_irodori_cache',
  'tests.test_tts_profiles',
  'tests.test_audio_quality',
  'tests.test_runtime_events',
  'tests.test_conversation_submission',
  'tests.test_live_conversation',
  'tests.test_live_http',
  'tests.test_installation_identity',
  'tests.test_gpu_arbiter',
  'tests.test_conversation_turn',
  'tests.test_voice_job_queue',
  'tests.test_voice_runtime',
  'tests.test_runtime_readiness',
  'tests.test_desktop_pet_config',
  'tests.test_desktop_pet',
  'tests.test_tray_controller',
  'tests.test_tray_controller_processes',
  'tests.test_tray_qt_runtime',
  'tests.test_setup_user_flow',
  'tests.test_venv_launcher_integrity',
  'tests.test_windows_gui_smoke_script',
  'tests.test_windows_process_identity',
  'tests.test_extension_reload_script',
];

function pythonCandidates() {
  const candidates = [];
  if (process.env.PYTHON) candidates.push(process.env.PYTHON);
  candidates.push(path.join(ROOT, 'local-api', '.venv', 'Scripts', 'python.exe'));
  candidates.push(path.join(ROOT, 'local-api', '.venv', 'bin', 'python'));
  if (process.platform === 'win32') {
    candidates.push('python');
    return candidates;
  }
  candidates.push('python', 'python3');
  return candidates;
}

function isLocalPath(command) {
  return path.isAbsolute(command) || command.includes(path.sep);
}

function resolvePython() {
  for (const command of pythonCandidates()) {
    if (isLocalPath(command) && (!fs.existsSync(command) || !fs.statSync(command).isFile())) continue;
    const probe = spawnSync(command, ['--version'], {
      cwd: ROOT,
      encoding: 'utf8',
      windowsHide: true,
      shell: false,
      timeout: 10000,
    });
    if (!probe.error && probe.status === 0) return command;
    if (probe.error?.code === 'ENOENT') continue;
    if (probe.error) {
      console.error(`Could not start Python candidate ${command}: ${probe.error.message}`);
      process.exit(1);
    }
  }
  return null;
}

const python = resolvePython();
if (!python) {
  console.error('Python was not found. Run setup-voice-env.cmd or install Python 3.11.');
  process.exit(1);
}

const timeoutMs = Number.parseInt(process.env.PYTHON_TEST_TIMEOUT_MS || '120000', 10);
if (!Number.isFinite(timeoutMs) || timeoutMs < 1000) {
  console.error(`Invalid PYTHON_TEST_TIMEOUT_MS: ${process.env.PYTHON_TEST_TIMEOUT_MS}`);
  process.exit(1);
}

for (const moduleName of TEST_MODULES) {
  const startedAt = Date.now();
  console.log(`[python-tests] START ${moduleName}`);
  const result = spawnSync(python, ['-m', 'unittest', '-v', moduleName], {
    cwd: ROOT,
    stdio: 'inherit',
    windowsHide: true,
    shell: false,
    timeout: timeoutMs,
  });
  if (result.error) {
    const elapsedSeconds = ((Date.now() - startedAt) / 1000).toFixed(1);
    if (result.error.code === 'ETIMEDOUT') {
      console.error(`[python-tests] TIMEOUT ${moduleName} after ${elapsedSeconds}s`);
    } else {
      console.error(`[python-tests] ERROR ${moduleName}: ${result.error.message}`);
    }
    process.exit(1);
  }
  if ((result.status ?? 1) !== 0) {
    console.error(`[python-tests] FAIL ${moduleName} (exit=${result.status ?? 1})`);
    process.exit(result.status ?? 1);
  }
  const elapsedSeconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log(`[python-tests] PASS ${moduleName} (${elapsedSeconds}s)`);
}

console.log(`[python-tests] PASS ${TEST_MODULES.length} modules`);
