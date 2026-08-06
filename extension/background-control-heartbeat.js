'use strict';

(function initBackgroundControlHeartbeat(global) {
  const ALARM_NAME = 'local-voice-control-heartbeat';

  function install(chromeObject, onWake) {
    const alarms = chromeObject && chromeObject.alarms;
    if (!alarms || typeof alarms.create !== 'function' || !alarms.onAlarm) return false;
    alarms.onAlarm.addListener((alarm) => {
      if (alarm && alarm.name === ALARM_NAME) onWake();
    });
    if (typeof alarms.get === 'function') {
      void Promise.resolve(alarms.get(ALARM_NAME))
        .then((existing) => {
          if (!existing) alarms.create(ALARM_NAME, { periodInMinutes: 1 });
        })
        .catch(() => alarms.create(ALARM_NAME, { periodInMinutes: 1 }));
    } else {
      alarms.create(ALARM_NAME, { periodInMinutes: 1 });
    }
    return true;
  }

  global.BackgroundControlHeartbeat = Object.freeze({ ALARM_NAME, install });
})(globalThis);
