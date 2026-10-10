'use strict';

const assert = require('node:assert/strict');
const fc = require('fast-check');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(ROOT, 'extension', 'manifest.json'), 'utf8'));
const { SETTINGS_VERSION, normalizeSettings } = require('../extension/options.js');

test('extension exposes a standard right-click options page', () => {
  assert.deepEqual(manifest.options_ui, {
    page: 'options.html',
    open_in_tab: true,
  });
  assert.ok(fs.existsSync(path.join(ROOT, 'extension', manifest.options_ui.page)));
  assert.ok(fs.existsSync(path.join(ROOT, 'extension', 'options.css')));
  const optionsSource = fs.readFileSync(path.join(ROOT, 'extension', 'options.js'), 'utf8');
  assert.match(optionsSource, /load\(\)\.then\(syncBrowserSettings\)/);
});

test('options settings preserve and clamp browser-only preview values', () => {
  assert.deepEqual(normalizeSettings({
    previewMaxLines: 10,
    previewMaxChars: 480,
    sttModel: 'large-v3-turbo',
    cancelGraceMs: 1500,
    liveTtsProfile: 'balanced',
  }), {
    settingsVersion: SETTINGS_VERSION,
    previewMaxLines: 10,
    previewMaxChars: 480,
  });

  assert.deepEqual(normalizeSettings({
    previewMaxLines: 99,
    previewMaxChars: 1,
  }), {
    settingsVersion: SETTINGS_VERSION,
    previewMaxLines: 20,
    previewMaxChars: 40,
  });
});

test('property: arbitrary preview settings always normalize into safe bounds', () => {
  fc.assert(
    fc.property(
      fc.anything(),
      fc.anything(),
      (previewMaxLines, previewMaxChars) => {
        const normalized = normalizeSettings({ previewMaxLines, previewMaxChars });
        assert.equal(normalized.settingsVersion, SETTINGS_VERSION);
        assert.ok(Number.isInteger(normalized.previewMaxLines));
        assert.ok(normalized.previewMaxLines >= 1 && normalized.previewMaxLines <= 20);
        assert.ok(Number.isInteger(normalized.previewMaxChars));
        assert.ok(normalized.previewMaxChars >= 40 && normalized.previewMaxChars <= 1000);
        assert.deepEqual(normalizeSettings(normalized), normalized);
      },
    ),
  );
});
