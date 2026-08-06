'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  cleanupStaleE2EProfiles,
  ownerPidFromName,
} = require('../scripts/e2e-profile-cleanup');

test('ownerPidFromName reads the first numeric owner segment across profile variants', () => {
  assert.equal(ownerPidFromName('.e2e-profile-mock-123-456'), 123);
  assert.equal(ownerPidFromName('.e2e-profile-auto-fixture-987-654-random'), 987);
  assert.equal(ownerPidFromName('.e2e-profile-external-777-3-654'), 777);
  assert.equal(ownerPidFromName('.e2e-profile-ref-pet-555-654-random'), 555);
  assert.equal(ownerPidFromName('.e2e-profile-manual'), 0);
  assert.equal(ownerPidFromName('other-profile-123-456'), 0);
});

test('cleanup removes dead owned profiles and old unowned profiles only', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'local-voice-e2e-cleanup-'));
  const now = 2_000_000_000_000;
  const dead = '.e2e-profile-mock-123-1000';
  const live = '.e2e-profile-mock-456-1001';
  const oldUnowned = '.e2e-profile-manual';
  const recentUnowned = '.e2e-profile-scratch';
  const unrelated = 'keep-me';

  try {
    for (const name of [dead, live, oldUnowned, recentUnowned, unrelated]) {
      fs.mkdirSync(path.join(root, name));
    }
    fs.utimesSync(path.join(root, oldUnowned), new Date(now - 2 * 86_400_000), new Date(now - 2 * 86_400_000));
    fs.utimesSync(path.join(root, recentUnowned), new Date(now - 1_000), new Date(now - 1_000));

    const result = cleanupStaleE2EProfiles(root, {
      now: () => now,
      isProcessAlive: (pid) => pid === 456,
    });

    assert.deepEqual(result, { scanned: 4, removed: 2, failed: 0 });
    assert.equal(fs.existsSync(path.join(root, dead)), false);
    assert.equal(fs.existsSync(path.join(root, oldUnowned)), false);
    assert.equal(fs.existsSync(path.join(root, live)), true);
    assert.equal(fs.existsSync(path.join(root, recentUnowned)), true);
    assert.equal(fs.existsSync(path.join(root, unrelated)), true);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
