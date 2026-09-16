#!/usr/bin/env node
// snap.cjs — deterministic full-page screenshot for the screenshot gate.
//
// Usage:
//   node snap.cjs --url <u> --out <png> [--width 1280] [--height 800]
//                 [--theme light|dark] [--wait-ms 500]
//
// Launches headless Chromium, emulates the requested color scheme, sets the
// viewport, disables CSS animations/transitions, waits for network idle plus
// --wait-ms, waits for document.fonts.ready, then takes a full-page
// screenshot to --out.
//
// Exit codes: 0 on success; 4 on any failure (the error is printed to stderr).

'use strict';

const { loadPlaywright } = require('./pw.cjs');

function parseArgs(argv) {
  const args = {
    width: 1280,
    height: 800,
    theme: 'light',
    waitMs: 500,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '--url':
        args.url = next();
        break;
      case '--out':
        args.out = next();
        break;
      case '--width':
        args.width = parseInt(next(), 10);
        break;
      case '--height':
        args.height = parseInt(next(), 10);
        break;
      case '--theme':
        args.theme = next();
        break;
      case '--wait-ms':
        args.waitMs = parseInt(next(), 10);
        break;
      default:
        throw new Error(`unknown argument: ${a}`);
    }
  }
  if (!args.url) throw new Error('--url is required');
  if (!args.out) throw new Error('--out is required');
  if (args.theme !== 'light' && args.theme !== 'dark') {
    throw new Error('--theme must be light or dark');
  }
  return args;
}

const DISABLE_ANIMATIONS_CSS = `
*, *::before, *::after {
  animation-duration: 0s !important;
  animation-delay: 0s !important;
  transition-duration: 0s !important;
  transition-delay: 0s !important;
  scroll-behavior: auto !important;
  caret-color: transparent !important;
}
`;

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { chromium } = loadPlaywright();

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
      colorScheme: args.theme,
    });
    const page = await context.newPage();
    await page.addInitScript((css) => {
      window.addEventListener('DOMContentLoaded', () => {
        const style = document.createElement('style');
        style.textContent = css;
        document.head.appendChild(style);
      });
    }, DISABLE_ANIMATIONS_CSS);

    await page.goto(args.url, { waitUntil: 'networkidle' });

    // Belt-and-suspenders: also inject the style directly in case the init
    // script's DOMContentLoaded listener missed it (already-loaded page).
    await page.addStyleTag({ content: DISABLE_ANIMATIONS_CSS });

    if (args.waitMs > 0) {
      await page.waitForTimeout(args.waitMs);
    }

    await page.evaluate(() => document.fonts && document.fonts.ready);

    await page.screenshot({ path: args.out, fullPage: true });
    await browser.close();
    process.exit(0);
  } catch (err) {
    try {
      await browser.close();
    } catch (_) {
      // ignore
    }
    throw err;
  }
}

main().catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(4);
});
