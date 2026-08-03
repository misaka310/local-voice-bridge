'use strict';

const path = require('path');
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: path.resolve(__dirname, '..', 'tests', 'e2e'),
  testMatch: ['extension-mock-ci.spec.js', 'assistant-text-extractor-dom.spec.js'],
  workers: 1,
});
