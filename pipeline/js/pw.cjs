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
  // 1. the project's own node_modules: resolve from the working directory, not
  //    from this file (which lives in the kit, whose parents hold no playwright)
  try {
    return require(require.resolve('playwright', { paths: [process.cwd()] }));
  } catch (err) { /* fall through */ }
  // 2. wherever this file's own resolution chain finds it
  try {
    return require('playwright');
  } catch (err) { /* fall through */ }
  // 3. the global install
  const globalRoot = execSync('npm root -g').toString().trim();
  return require(path.join(globalRoot, 'playwright'));
}

module.exports = { loadPlaywright };
