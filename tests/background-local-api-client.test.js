'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.resolve(__dirname, '../extension/background-local-api-client.js'),
  'utf8',
);

function createClient(posts, configuration = {}) {
  const context = vm.createContext({ URL, Uint8Array, btoa, console });
  context.globalThis = context;
  vm.runInContext(SOURCE, context, { filename: 'background-local-api-client.js' });
  return context.BackgroundLocalApiClient.create({
    fetch: async (url, options = {}) => {
      const requestBody = JSON.parse(options.body || '{}');
      posts.push(requestBody);
      if (configuration.missingReplayAudio && String(url).endsWith('/v1/playback/replay')) {
        return {
          ok: false,
          status: 409,
          async json() {
            return { ok: false, error: 'no replay audio is available' };
          },
        };
      }
      return {
        ok: true,
        status: 200,
        async json() {
          return {
            ok: true,
            audioUrl: 'http://127.0.0.1:8717/audio/test.wav',
            voiceProfile: 'irodori-v3',
            referenceVoice: requestBody.referenceVoice || '',
            usedReferenceAudio: requestBody.referenceVoice ? 'sample.wav' : '',
          };
        },
      };
    },
    getSettings: async () => ({
      apiUrl: 'http://127.0.0.1:8717/v1/speak',
      healthUrl: 'http://127.0.0.1:8717/health',
      voiceVolume: 0.6,
      referenceVoice: '',
    }),
    defaultSettings: { voiceProfile: 'irodori-v3' },
  });
}

test('speak preserves browser-generation default and allows explicit local playback', async () => {
  const posts = [];
  const client = createClient(posts);

  await client.speak('default', 'request-1', 'irodori-v3', '', '');
  await client.speak('local', 'request-2', 'irodori-v3', '', '', true);

  assert.equal(posts[0].playLocal, false);
  assert.equal(posts[1].playLocal, true);
});

test('replay regenerates the saved answer when the local audio cache is gone', async () => {
  const posts = [];
  const client = createClient(posts, { missingReplayAudio: true });

  const result = await client.replayLocalAudio('保存済みの回答です。', {
    id: 'q-saved-1',
    voiceProfile: 'irodori-v3',
    referenceVoice: 'asuka',
    voicePrompt: '',
  });

  assert.equal(posts.length, 2);
  assert.equal(posts[0].text, '保存済みの回答です。');
  assert.equal(posts[1].text, '保存済みの回答です。');
  assert.equal(posts[1].playLocal, true);
  assert.match(posts[1].requestId, /^replay-q-saved-1-/);
  assert.equal(posts[1].referenceVoice, 'asuka');
  assert.equal(result.audioUrl, 'http://127.0.0.1:8717/audio/test.wav');
});
