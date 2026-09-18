#!/usr/bin/env node
// tokens.cjs — sample computed styles from a live page for design-token
// extraction (pipeline Stage 4 — /jg-ux, "Tokens").
//
// Usage:
//   node tokens.cjs --url <u> [--viewport 1280x800]
//
// Loads Playwright via `require('playwright')`, falling back to the
// globally installed package (`npm root -g`/playwright) when the local
// resolution fails. Opens --url headless, waits for network idle and
// document.fonts.ready, then samples computed styles of body, h1..h4, p, a,
// button, input/textarea/select, nav, header, footer, [class*=card], and up
// to 400 other elements in document order. Prints ONE JSON object to
// stdout:
//
//   {
//     "fonts": [{"family": "...", "count": n, "roles": ["body", ...]}],
//     "type_scale": [{"px": n, "count": n}],
//     "spacing_scale": [{"px": n, "count": n}],
//     "colors": [{"value": "rgb(...)", "count": n, "roles": ["text", ...]}],
//     "radii": [{"value": "...", "count": n}],
//     "shadows": [{"value": "...", "count": n}],
//     "line_heights": [{"value": "...", "count": n}],
//     "weights": [{"value": "...", "count": n}]
//   }
//
// Exit codes: 0 on success; any error is printed to stderr and exits 4.

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

function parseArgs(argv) {
  const args = { viewport: '1280x800' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '--url':
        args.url = next();
        break;
      case '--viewport':
        args.viewport = next();
        break;
      default:
        throw new Error(`unknown argument: ${a}`);
    }
  }
  if (!args.url) throw new Error('--url is required');
  const m = /^(\d+)x(\d+)$/.exec(args.viewport);
  if (!m) throw new Error('--viewport must look like 1280x800');
  args.width = parseInt(m[1], 10);
  args.height = parseInt(m[2], 10);
  return args;
}

// Runs inside the page via page.evaluate — no Node globals available here.
/* istanbul ignore next */
function sampleStyles() {
  const SELECTOR =
    'body, h1, h2, h3, h4, p, a, button, input, textarea, select, nav, header, footer, [class*=card]';
  const MAX_OTHER = 400;

  const priority = new Set();
  document.querySelectorAll(SELECTOR).forEach((el) => priority.add(el));

  const all = Array.from(document.querySelectorAll('*'));
  const others = [];
  for (const el of all) {
    if (!priority.has(el)) others.push(el);
    if (others.length >= MAX_OTHER) break;
  }

  const elements = Array.from(priority).concat(others);

  function bump(map, key, role) {
    if (key === undefined || key === null || key === '') return;
    let entry = map.get(key);
    if (!entry) {
      entry = { count: 0, roles: new Set() };
      map.set(key, entry);
    }
    entry.count += 1;
    if (role) entry.roles.add(role);
  }

  function roleForFont(tag) {
    if (/^h[1-4]$/.test(tag)) return 'heading';
    if (tag === 'code' || tag === 'pre' || tag === 'kbd' || tag === 'samp') return 'mono';
    return 'body';
  }

  function pxNumber(v) {
    if (!v) return null;
    const m = /^(-?\d+(?:\.\d+)?)px$/.exec(v.trim());
    if (!m) return null;
    return parseFloat(m[1]);
  }

  function isTransparent(v) {
    if (!v) return true;
    return (
      v === 'transparent' ||
      v === 'rgba(0, 0, 0, 0)' ||
      /rgba?\([^)]*,\s*0\s*\)$/.test(v)
    );
  }

  const fonts = new Map();
  const typeScale = new Map();
  const spacing = new Map();
  const colors = new Map();
  const radii = new Map();
  const shadows = new Map();
  const lineHeights = new Map();
  const weights = new Map();

  for (const el of elements) {
    const tag = el.tagName ? el.tagName.toLowerCase() : '';
    const cs = window.getComputedStyle(el);

    // fonts
    const family = (cs.fontFamily || '').trim();
    bump(fonts, family, roleForFont(tag));

    // type scale
    const fs = pxNumber(cs.fontSize);
    if (fs !== null) bump(typeScale, fs, null);

    // line heights
    if (cs.lineHeight) bump(lineHeights, cs.lineHeight, null);

    // weights
    if (cs.fontWeight) bump(weights, cs.fontWeight, null);

    // spacing: margin + padding + gap
    const spacingProps = [
      'marginTop',
      'marginRight',
      'marginBottom',
      'marginLeft',
      'paddingTop',
      'paddingRight',
      'paddingBottom',
      'paddingLeft',
      'rowGap',
      'columnGap',
    ];
    for (const prop of spacingProps) {
      const v = pxNumber(cs[prop]);
      if (v !== null && v > 0) bump(spacing, v, null);
    }

    // colors: text, background, border
    if (!isTransparent(cs.color)) bump(colors, cs.color, 'text');
    if (!isTransparent(cs.backgroundColor)) bump(colors, cs.backgroundColor, 'background');
    const borderColors = [
      cs.borderTopColor,
      cs.borderRightColor,
      cs.borderBottomColor,
      cs.borderLeftColor,
    ];
    const borderWidths = [
      cs.borderTopWidth,
      cs.borderRightWidth,
      cs.borderBottomWidth,
      cs.borderLeftWidth,
    ];
    for (let i = 0; i < borderColors.length; i++) {
      const w = pxNumber(borderWidths[i]);
      if (w && w > 0 && !isTransparent(borderColors[i])) {
        bump(colors, borderColors[i], 'border');
      }
    }

    // radii
    if (cs.borderRadius && cs.borderRadius !== '0px') bump(radii, cs.borderRadius, null);

    // shadows
    if (cs.boxShadow && cs.boxShadow !== 'none') bump(shadows, cs.boxShadow, null);
  }

  function toSortedArray(map, keyName, sortNumeric) {
    const arr = Array.from(map.entries()).map(([key, entry]) => {
      const out = { [keyName]: key, count: entry.count };
      if (entry.roles.size) out.roles = Array.from(entry.roles);
      return out;
    });
    if (sortNumeric) {
      arr.sort((a, b) => a[keyName] - b[keyName]);
    } else {
      arr.sort((a, b) => b.count - a.count);
    }
    return arr;
  }

  const fontsOut = toSortedArray(fonts, 'family', false);
  const typeScaleOut = toSortedArray(typeScale, 'px', true);
  const spacingOut = toSortedArray(spacing, 'px', true);
  let colorsOut = toSortedArray(colors, 'value', false);
  colorsOut = colorsOut.slice(0, 12);
  const radiiOut = toSortedArray(radii, 'value', false);
  let shadowsOut = toSortedArray(shadows, 'value', false);
  shadowsOut = shadowsOut.slice(0, 5);
  const lineHeightsOut = toSortedArray(lineHeights, 'value', false);
  const weightsOut = toSortedArray(weights, 'value', false);

  return {
    fonts: fontsOut,
    type_scale: typeScaleOut,
    spacing_scale: spacingOut,
    colors: colorsOut,
    radii: radiiOut,
    shadows: shadowsOut,
    line_heights: lineHeightsOut,
    weights: weightsOut,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const { chromium } = loadPlaywright();

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      viewport: { width: args.width, height: args.height },
    });
    const page = await context.newPage();
    await page.goto(args.url, { waitUntil: 'networkidle' });
    await page.evaluate(() => document.fonts && document.fonts.ready);

    const result = await page.evaluate(sampleStyles);

    await browser.close();
    process.stdout.write(JSON.stringify(result));
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
