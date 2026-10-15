'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const { createLedger, normalizeDeliveryId } = require('../extension/delivery-id-core.js');

function createStorage(initial = {}) {
  const values = { ...initial };
  return {
    getItem(key) { return Object.prototype.hasOwnProperty.call(values, key) ? values[key] : null; },
    setItem(key, value) { values[key] = String(value); },
    values,
  };
}

test('delivery IDs are persisted and duplicate delivery is recognized after recreation', () => {
  const storage = createStorage();
  const first = createLedger(storage);

  assert.equal(first.has('delivery-1'), false);
  assert.equal(first.mark('delivery-1'), true);
  assert.equal(first.has('delivery-1'), true);

  const recreated = createLedger(storage);
  assert.equal(recreated.has('delivery-1'), true);
});

test('delivery ledger is bounded and keeps the newest IDs', () => {
  const storage = createStorage();
  const ledger = createLedger(storage, { limit: 3 });

  for (const id of ['one', 'two', 'three', 'four']) ledger.mark(id);

  assert.deepEqual(ledger.snapshot(), ['two', 'three', 'four']);
  assert.equal(ledger.has('one'), false);
});

test('invalid delivery IDs are ignored', () => {
  const storage = createStorage();
  const ledger = createLedger(storage);

  assert.equal(normalizeDeliveryId('bad id'), '');
  assert.equal(ledger.mark('bad id'), false);
  assert.deepEqual(ledger.snapshot(), []);
});
