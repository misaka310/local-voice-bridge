#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const trayPath = path.join(ROOT, 'local-api', 'tray_controller.py');
const onboardingPath = path.join(ROOT, 'local-api', 'control_panel_onboarding.py');
const failures = [];

if (!fs.existsSync(onboardingPath)) {
  failures.push('local-api/control_panel_onboarding.py: focused first-run module is missing');
}

const tray = fs.readFileSync(trayPath, 'utf8');
const onboarding = fs.existsSync(onboardingPath) ? fs.readFileSync(onboardingPath, 'utf8') : '';

for (const forbidden of ['conversation_settings_timer', 'pet_settings_timer', 'setInterval(500)']) {
  if (tray.includes(forbidden)) {
    failures.push(`local-api/tray_controller.py: fixed tray polling returned (${forbidden})`);
  }
}

for (const required of [
  'FirstRunControlPanel',
  'self.control_panel.snapshot_applied.connect(self.sync_runtime_from_snapshot)',
  'def sync_runtime_from_snapshot(',
]) {
  if (!tray.includes(required)) {
    failures.push(`local-api/tray_controller.py: missing snapshot-driven contract (${required})`);
  }
}

for (const required of [
  'class OnboardingStateStore',
  'class FirstRunControlPanel',
  'control-panel-onboarding.json',
  'test_speech',
  'snapshot_applied = Signal(object)',
]) {
  if (!onboarding.includes(required)) {
    failures.push(`local-api/control_panel_onboarding.py: missing onboarding contract (${required})`);
  }
}

if (failures.length) {
  for (const failure of failures) console.error(`ARCHITECTURE FAIL: ${failure}`);
  process.exit(1);
}

console.log('TRAY SNAPSHOT ARCHITECTURE CHECK: PASS');
