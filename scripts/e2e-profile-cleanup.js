'use strict';

const fs = require('node:fs');
const path = require('node:path');

const PROFILE_PREFIX = '.e2e-profile-';
const DEFAULT_UNOWNED_MAX_AGE_MS = 24 * 60 * 60 * 1000;

function ownerPidFromName(name) {
  const value = String(name || '');
  if (!value.startsWith(PROFILE_PREFIX)) return 0;
  const pidToken = value
    .slice(PROFILE_PREFIX.length)
    .split('-')
    .find((part) => /^\d+$/.test(part));
  const pid = Number(pidToken || 0);
  return Number.isInteger(pid) && pid > 0 ? pid : 0;
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return Boolean(error && error.code === 'EPERM');
  }
}

function cleanupStaleE2EProfiles(root, options = {}) {
  const fsApi = options.fs || fs;
  const now = typeof options.now === 'function' ? options.now : Date.now;
  const processAlive = typeof options.isProcessAlive === 'function'
    ? options.isProcessAlive
    : isProcessAlive;
  const unownedMaxAgeMs = Math.max(
    60_000,
    Number(options.unownedMaxAgeMs || DEFAULT_UNOWNED_MAX_AGE_MS),
  );
  const result = { scanned: 0, removed: 0, failed: 0 };
  let entries = [];
  try {
    entries = fsApi.readdirSync(root, { withFileTypes: true });
  } catch (_error) {
    result.failed += 1;
    return result;
  }

  for (const entry of entries) {
    if (!entry || !entry.isDirectory() || !entry.name.startsWith(PROFILE_PREFIX)) continue;
    result.scanned += 1;
    const profilePath = path.join(root, entry.name);
    const ownerPid = ownerPidFromName(entry.name);
    let remove = ownerPid > 0 ? !processAlive(ownerPid) : false;
    if (!ownerPid) {
      try {
        const modifiedAt = Number(fsApi.statSync(profilePath).mtimeMs || 0);
        remove = now() - modifiedAt >= unownedMaxAgeMs;
      } catch (_error) {
        result.failed += 1;
        continue;
      }
    }
    if (!remove) continue;
    try {
      fsApi.rmSync(profilePath, { recursive: true, force: true });
      result.removed += 1;
    } catch (_error) {
      result.failed += 1;
    }
  }
  return result;
}

module.exports = Object.freeze({
  DEFAULT_UNOWNED_MAX_AGE_MS,
  PROFILE_PREFIX,
  cleanupStaleE2EProfiles,
  isProcessAlive,
  ownerPidFromName,
});
