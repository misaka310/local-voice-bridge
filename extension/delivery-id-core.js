'use strict';

(function exposeDeliveryIdCore(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LocalVoiceDeliveryIds = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  function normalizeDeliveryId(value) {
    const normalized = String(value || '').trim();
    return /^[A-Za-z0-9._:-]{1,128}$/.test(normalized) ? normalized : '';
  }

  function createLedger(storage, { key = 'localVoiceAppliedDeliveryIds', limit = 128 } = {}) {
    const safeLimit = Math.max(1, Math.min(512, Number(limit) || 128));
    let values = [];

    try {
      const parsed = JSON.parse(String(storage && storage.getItem(key) || '[]'));
      if (Array.isArray(parsed)) {
        values = parsed.map(normalizeDeliveryId).filter(Boolean).slice(-safeLimit);
      }
    } catch (_error) {
      values = [];
    }

    function persist() {
      try {
        if (storage && typeof storage.setItem === 'function') {
          storage.setItem(key, JSON.stringify(values));
        }
      } catch (_error) {}
    }

    return {
      has(value) {
        const deliveryId = normalizeDeliveryId(value);
        return Boolean(deliveryId && values.includes(deliveryId));
      },
      mark(value) {
        const deliveryId = normalizeDeliveryId(value);
        if (!deliveryId) return false;
        values = values.filter((item) => item !== deliveryId);
        values.push(deliveryId);
        if (values.length > safeLimit) values = values.slice(-safeLimit);
        persist();
        return true;
      },
      snapshot() {
        return values.slice();
      },
    };
  }

  return { normalizeDeliveryId, createLedger };
});
