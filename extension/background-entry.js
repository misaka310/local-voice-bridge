'use strict';

// Keep the existing service-worker runtime intact while replacing its pure helpers
// with a separately testable module. The assignments happen after background.js is
// evaluated because classic worker function declarations create writable globals.
importScripts(
  'background-core.js',
  'background-settings-core.js',
  'background-runtime-core.js',
  'background-queue-core.js',
  'background-auto-recheck.js',
  'background-state-publisher.js',
  'background-control-poll-policy.js',
  'background-control-sync.js',
  'background-control-heartbeat.js',
  'background-tab-registry.js',
  'background-tab-reconnect.js',
  'background-conversation-target.js',
  'background-local-api-client.js',
  'background-network-monitor.js',
  'background-runtime-store.js',
  'background-playback-queue.js',
  'background-live-client.js',
  'background-message-router.js',
  'background.js',
);

for (const [name, implementation] of Object.entries(globalThis.BackgroundCore || {})) {
  globalThis[name] = implementation;
}
