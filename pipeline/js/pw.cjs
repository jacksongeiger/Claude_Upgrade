// pw.cjs — Playwright loader.
//
// Usage: const { loadPlaywright } = require('./pw.cjs'); const pw = loadPlaywright();
//
// Tries the local `require('playwright')` resolution first (a project's own
// node_modules); if that fails, falls back to the globally installed
// playwright package (`npm root -g`/playwright), which is what this
// environment ships.
'use strict';

const path = require('path');
const { execSync } = require('child_process');

function loadPlaywright() {
  try {
    return require('playwright');
  } catch (err) {
    const globalRoot = execSync('npm root -g').toString().trim();
    return require(path.join(globalRoot, 'playwright'));
  }
}

module.exports = { loadPlaywright };
