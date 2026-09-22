#!/usr/bin/env node
// ui_measure.cjs — measure the design a page actually rendered (/jg-ui).
//
// Usage:
//   node ui_measure.cjs --url <u> [--url <u2> ...] --out <dir>
//        [--viewports 1440x900,375x812] [--themes light,dark] [--scroll] [--stress]
//        [--full] [--wait-ms 800] [--timeout-ms 45000] [--no-shots] [--no-focus]
//
// One capture per url × viewport × theme, written as <out>/<slug>-<w>-<theme>.json
// plus a viewport screenshot .png (animations frozen; --full adds .full.png,
// --stress adds .stress.png). Stdout: one JSON line per capture,
// {"url","viewport","theme","json","png","ok","error"}.
//
// Every number comes from getComputedStyle on elements that are really
// visible — the method of the ui-design skill's extract.js — with its two
// measured bugs fixed: a cubic-bezier() is one easing, not four, and the body
// size is the size carrying the most characters of running copy, not the
// most elements. The same script reads a reference site, the project's
// localhost and a file:// page, which is what makes them comparable. Fixed
// consent and chat overlays are hidden, never clicked.
//
// Exit: 0 every capture ran · 4 any capture failed (the rest still write) · 1 usage.

'use strict';

const fs = require('fs');
const path = require('path');
const { loadPlaywright } = require('./pw.cjs');

const DESKTOP_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
const MOBILE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1';

function parseArgs(argv) {
  const a = { urls: [], viewports: ['1440x900'], themes: ['light'], waitMs: 800, timeoutMs: 45000,
              scroll: false, stress: false, full: false, shots: true, focus: true, out: null };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i], next = () => argv[++i];
    switch (k) {
      case '--url': a.urls.push(next()); break;
      case '--out': a.out = next(); break;
      case '--viewports': a.viewports = next().split(',').filter(Boolean); break;
      case '--themes': a.themes = next().split(',').filter(Boolean); break;
      case '--wait-ms': a.waitMs = +next(); break;
      case '--timeout-ms': a.timeoutMs = +next(); break;
      case '--scroll': a.scroll = true; break;
      case '--stress': a.stress = true; break;
      case '--full': a.full = true; break;
      case '--no-shots': a.shots = false; break;
      case '--no-focus': a.focus = false; break;
      default: throw new Error(`unknown argument: ${k}`);
    }
  }
  if (!a.urls.length || !a.out) throw new Error('--url and --out are required');
  for (const v of a.viewports) if (!/^\d+x\d+$/.test(v)) throw new Error(`bad viewport ${v}`);
  for (const t of a.themes) if (!['light', 'dark'].includes(t)) throw new Error(`bad theme ${t}`);
  return a;
}

function slugOf(u) {
  try {
    const p = new URL(u);
    const raw = p.protocol === 'file:' ? path.basename(p.pathname) + p.hash : p.host + p.pathname + p.hash;
    return raw.replace(/[^a-z0-9]+/gi, '-').replace(/^-+|-+$/g, '').toLowerCase().slice(0, 60) || 'page';
  } catch (e) { return 'page'; }
}

// ------------------------------------------------------------------ in page
// Everything below runs inside the page and may not reference Node scope.

function hideOverlays() {
  const hidden = [];
  const RX = /cookie|consent|privacy choices|we use cookies|accept all|reject all|chat now|sales reps|live chat|talk to sales/i;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length && t.length < 600 && RX.test(t)) { el.style.setProperty('display', 'none', 'important'); hidden.push(t.slice(0, 100)); }
  }
  for (const f of document.querySelectorAll('iframe[src*="intercom"],iframe[src*="drift"],iframe[src*="hubspot"],iframe[src*="zendesk"],iframe[src*="crisp"]')) {
    f.style.setProperty('display', 'none', 'important'); hidden.push('chat widget iframe');
  }
  return [...new Set(hidden)].slice(0, 5);
}

function measurePage() {
  const MAX = 6000;
  const vw = innerWidth, vh = innerHeight;
  const round = (x, d = 0) => { const m = 10 ** d; return Math.round(x * m) / m; };
  const px = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : null; };
  const median = (a) => { if (!a.length) return null; const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
  const visible = (el, cs) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    cs = cs || getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0.05;
  };
  const ownText = (el) => { let t = ''; for (const n of el.childNodes) if (n.nodeType === 3) t += n.textContent; return t.replace(/\s+/g, ' ').trim(); };
  const short = (el) => {
    const bits = [];
    for (let n = el, i = 0; n && n.nodeType === 1 && n !== document.body && i < 3; n = n.parentElement, i++) {
      let b = n.tagName.toLowerCase();
      if (n.id) b += '#' + n.id;
      else if (typeof n.className === 'string' && n.className.trim()) b += '.' + n.className.trim().split(/\s+/).slice(0, 2).join('.');
      bits.unshift(b);
    }
    return bits.join(' > ').slice(0, 120);
  };
  const add = (m, k, n = 1) => m.set(k, (m.get(k) || 0) + n);
  const top = (m, n) => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([value, count]) => ({ value, count }));
  const total = (m) => [...m.values()].reduce((a, c) => a + c, 0);
  const lines = (el) => { const rg = document.createRange(); rg.selectNodeContents(el); return new Set([...rg.getClientRects()].filter((x) => x.width > 2).map((x) => Math.round(x.top))).size; };

  // ---- colour: any CSS colour -> [r,g,b,a] through a 1×1 canvas (oklch, lab, p3 included)
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  const cache = new Map();
  const rgba = (c) => {
    if (!c) return null;
    if (cache.has(c)) return cache.get(c);
    let out;
    if (c === 'transparent' || c === 'rgba(0, 0, 0, 0)') out = [0, 0, 0, 0];
    else {
      cx.clearRect(0, 0, 1, 1); cx.fillStyle = '#000'; cx.fillStyle = c; cx.fillRect(0, 0, 1, 1);
      const d = cx.getImageData(0, 0, 1, 1).data; out = [d[0], d[1], d[2], d[3] / 255];
    }
    cache.set(c, out); return out;
  };
  const hex = (c) => '#' + c.slice(0, 3).map((v) => v.toString(16).padStart(2, '0')).join('');
  const lin = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const over = (t, b) => { const a = t[3]; return [0, 1, 2].map((i) => Math.round(t[i] * a + b[i] * (1 - a))).concat(1); };
  const hsl = (c) => {
    const [r, g, b] = c.slice(0, 3).map((v) => v / 255);
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn, l = (mx + mn) / 2;
    if (d < 1e-6) return [0, 0, l];
    const s = d / (1 - Math.abs(2 * l - 1));
    const h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [(h * 60 + 360) % 360, s, l];
  };
  // chroma in OKLCH, not HSL saturation: a cool-tinted near-white has HSL
  // saturation 0.3 but OKLCH chroma 0.01, and it is a neutral
  const oklch = (c) => {
    const [r, g, b] = c.slice(0, 3).map(lin);
    const l_ = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m_ = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s_ = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    const L = 0.2104542553 * l_ + 0.793617785 * m_ - 0.0040720468 * s_;
    const A = 1.9779984951 * l_ - 2.428592205 * m_ + 0.4505937099 * s_;
    const B = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.808675766 * s_;
    return [L, Math.hypot(A, B), (Math.atan2(B, A) * 180 / Math.PI + 360) % 360];
  };
  const chromatic = (c) => { const [L, C] = oklch(c); return C >= 0.045 && L > 0.15 && L < 0.97; };
  const base = (() => {
    for (const n of [document.body, document.documentElement]) { const c = rgba(getComputedStyle(n).backgroundColor); if (c && c[3] > 0.99) return c; }
    return [255, 255, 255, 1];
  })();
  const bgOf = (el) => {
    const layers = [];
    for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
      const cs = getComputedStyle(n);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return null;  // text over a gradient or image: unknown
      const c = rgba(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] > 0.99) break; }
    }
    let acc = layers.length && layers[layers.length - 1][3] > 0.99 ? layers.pop() : base;
    while (layers.length) acc = over(layers.pop(), acc);
    return acc;
  };

  // ---- the element pass
  const els = [];
  const all = document.querySelectorAll('body *');
  for (let i = 0; i < all.length && els.length < MAX; i++) {
    const el = all[i];
    if (el.closest('#jgui-picker')) continue;  // the kit's own picker chrome
    const cs = getComputedStyle(el);
    if (visible(el, cs)) els.push([el, cs]);
  }

  const families = new Map(), sizeChars = new Map(), sizeEls = new Map(), copyChars = new Map(), weightChars = new Map();
  const leading = new Map(), trackingT = new Map(), pairs = new Map(), spacing = new Map(), radii = new Map(), shadows = new Map();
  const borders = new Map(), textColors = new Map(), bgColors = new Map(), widths = new Map(), durations = new Map(), easings = new Map();
  const animations = new Map(), contrastFail = new Map();
  let chars = 0, centered = 0, transAll = 0, layoutTrans = 0, easeIn = 0, bounce = 0, slow = 0, contrastChecked = 0, contrastUnknown = 0;
  let tightLeading = 0, wideTracking = 0, gradientText = 0, sideStripes = 0, glow = 0, emDashes = 0;
  const ex = { slow: [], easeIn: [], layout: [], bounce: [], tight: [], tracking: [], emoji: [], placeholders: [] };
  const SPACE = ['gap', 'rowGap', 'columnGap', 'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight', 'marginTop', 'marginBottom'];
  const LAYOUT = /^(width|height|top|left|right|bottom|inset|margin|padding|max-width|max-height|min-width|min-height)/;
  const EMOJI = /\p{Extended_Pictographic}/u;
  const PLACEHOLDER = /\b(lorem ipsum|john doe|jane doe|acme( corp| inc)?|example\.com|99\.9+%|trusted by \d[\d,]*\+)/i;
  const splitTop = (s) => { const out = []; let d = 0, cur = ''; for (const ch of s || '') { if (ch === '(') d++; if (ch === ')') d--; if (ch === ',' && !d) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; };
  const toMs = (s) => { const n = parseFloat(s); return !Number.isFinite(n) ? 0 : /ms$/.test(s) ? n : n * 1000; };
  const bez = (e) => { const m = /cubic-bezier\(([^)]+)\)/.exec(e); return m ? m[1].split(',').map(Number) : null; };
  const push = (arr, v) => { if (arr.length < 5) arr.push(v); };

  for (const [el, cs] of els) {
    add(families, cs.fontFamily.split(',')[0].replace(/["']/g, '').trim());
    for (const p of SPACE) { const v = px(cs[p]); if (v && v > 0 && v <= 240) add(spacing, round(v, 1)); }
    if (cs.borderRadius !== '0px') add(radii, cs.borderRadius);
    if (cs.boxShadow !== 'none') {
      add(shadows, cs.boxShadow);
      for (const layer of splitTop(cs.boxShadow)) {
        const col = (layer.match(/(rgba?\([^)]+\)|oklch\([^)]+\)|#[0-9a-f]{3,8})/i) || [])[0];
        const nums = (layer.replace(/(rgba?|oklch)\([^)]+\)/gi, '').match(/-?\d+(\.\d+)?px/g) || []).map(parseFloat);
        const c = col && rgba(col);
        if (c && nums.length >= 3 && nums[2] >= 12 && c[3] >= 0.15 && hsl(c)[1] >= 0.4) { glow++; break; }
      }
    }
    const bw = px(cs.borderTopWidth); if (bw && bw > 0 && cs.borderTopStyle !== 'none') add(borders, `${cs.borderTopWidth} ${cs.borderTopColor}`);
    if ((px(cs.borderLeftWidth) || 0) >= 3 && (px(cs.borderTopWidth) || 0) < 1 && (px(cs.borderTopLeftRadius) || 0) >= 4) sideStripes++;
    const bg = cs.backgroundColor; if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') add(bgColors, bg);
    const mw = px(cs.maxWidth); if (mw && mw >= 400 && mw <= 2000) add(widths, mw);
    const props = splitTop(cs.transitionProperty), durs = splitTop(cs.transitionDuration), eases = splitTop(cs.transitionTimingFunction);
    durs.forEach((d, i) => {
      const t = toMs(d); if (!t) return;
      const prop = props[i % Math.max(1, props.length)] || 'all', e = eases[i % Math.max(1, eases.length)] || 'ease';
      add(durations, `${round(t)}ms`); add(easings, e);
      if (prop === 'all') transAll++;
      if (LAYOUT.test(prop)) { layoutTrans++; push(ex.layout, `${short(el)}: ${prop}`); }
      const b = bez(e);
      if (e === 'ease-in' || (b && b[0] >= 0.3 && b[1] <= 0.1 && b[2] >= 0.8 && b[3] >= 0.9)) { easeIn++; push(ex.easeIn, `${short(el)}: ${prop} ${e}`); }
      if (b && (b[1] < -0.1 || b[1] > 1.1 || b[3] < -0.1 || b[3] > 1.1)) { bounce++; push(ex.bounce, `${short(el)}: ${e}`); }
      if (t > 300) { slow++; push(ex.slow, `${short(el)}: ${prop} ${round(t)}ms`); }
    });
    if (cs.animationName && cs.animationName !== 'none') add(animations, `${cs.animationName} ${cs.animationDuration}`);
    if (/text/.test(cs.backgroundClip || cs.webkitBackgroundClip || '') && (cs.color === 'rgba(0, 0, 0, 0)' || cs.webkitTextFillColor === 'rgba(0, 0, 0, 0)')) gradientText++;

    const t = ownText(el);
    if (!t) continue;
    const size = px(cs.fontSize), n = t.length;
    chars += n;
    add(sizeChars, size, n); add(sizeEls, size); add(weightChars, cs.fontWeight, n); add(pairs, `${size}px/${cs.fontWeight}`);
    const lh = px(cs.lineHeight);
    if (lh && size) {
      add(leading, `${(lh / size).toFixed(2)}x @ ${size}px`, n);
      if (size >= 13 && size <= 20 && lh / size < 1.25 && n >= 60 && lines(el) >= 2) { tightLeading++; push(ex.tight, `${short(el)} ${size}px/${(lh / size).toFixed(2)}`); }
    }
    if (cs.letterSpacing !== 'normal') {
      add(trackingT, `${cs.letterSpacing} @ ${size}px`, n);
      const em = px(cs.letterSpacing) / size;
      if (size <= 20 && em > 0.05 && n >= 20) { wideTracking++; push(ex.tracking, `${short(el)} ${round(em, 3)}em`); }
    }
    if (cs.textAlign === 'center') centered += n;
    add(textColors, cs.color, n);
    emDashes += (t.match(/—/g) || []).length;
    if (PLACEHOLDER.test(t)) push(ex.placeholders, `${short(el)}: ${t.slice(0, 50)}`);
    const blk = el.closest('p, li, dd, blockquote');
    if (blk && lines(blk) >= 2) add(copyChars, size, n);
    if (EMOJI.test(t) && el.closest('button, a, h1, h2, h3, h4, h5, h6, li, th, td, label, nav, [role=button]')) push(ex.emoji, `${short(el)}: ${t.slice(0, 40)}`);
    const fg = rgba(cs.color), back = bgOf(el);
    if (!fg || !back) { contrastUnknown++; continue; }
    const flat = fg[3] < 1 ? over(fg, back) : fg;
    const r = ratio(flat, back), large = size >= 24 || (size >= 18.66 && +cs.fontWeight >= 700), need = large ? 3 : 4.5;
    contrastChecked++;
    if (r < need) {
      const key = `${hex(flat)} on ${hex(back)} ${need}`;
      const cur = contrastFail.get(key) || { fg: hex(flat), bg: hex(back), ratio: round(r, 2), need, count: 0, example: `${short(el)}: "${t.slice(0, 40)}"` };
      cur.count++; contrastFail.set(key, cur);
    }
  }

  // ---- type numbers
  const copyTop = [...copyChars.entries()].sort((a, b) => b[1] - a[1]);
  const readTop = [...sizeChars.entries()].filter(([s]) => s >= 11 && s <= 22).sort((a, b) => b[1] - a[1]);
  const body = (copyTop[0] || readTop[0] || [null])[0];
  const sizesUsed = [...sizeChars.entries()].filter(([, c]) => c >= chars * 0.005).map(([s]) => s).sort((a, b) => a - b);
  const ladder = sizesUsed.filter((s) => body && s >= body);
  const steps = []; for (let i = 1; i < ladder.length; i++) steps.push(ladder[i] / ladder[i - 1]);
  const scaleRatio = ladder.length > 1 ? (ladder[ladder.length - 1] / ladder[0]) ** (1 / (ladder.length - 1)) : null;
  const SCALES = [[1.067, 'minor second'], [1.125, 'major second'], [1.2, 'minor third'], [1.25, 'major third'], [1.333, 'perfect fourth'], [1.414, 'augmented fourth'], [1.5, 'perfect fifth'], [1.618, 'golden ratio']];
  const nearestScale = scaleRatio ? SCALES.reduce((b, s) => (Math.abs(s[0] - scaleRatio) < Math.abs(b[0] - scaleRatio) ? s : b))[1] : null;
  const weightsTop = [...weightChars.entries()].sort((a, b) => b[1] - a[1]);

  // ---- spacing: grid adherence by use, and the short list that carries 90% of it
  const spTotal = total(spacing);
  const onGrid = (g) => (spTotal ? round(100 * [...spacing.entries()].filter(([v]) => v % g === 0).reduce((a, [, c]) => a + c, 0) / spTotal) : null);
  let covered = 0; const carry = [];
  for (const [v, c] of [...spacing.entries()].sort((a, b) => b[1] - a[1])) { if (covered >= spTotal * 0.9) break; carry.push(v); covered += c; }

  // ---- colour: which colours carry meaning, and how many hues
  const accents = new Set(), hues = new Set();
  for (const c of [...textColors.keys(), ...bgColors.keys()].map(rgba).filter(Boolean)) {
    if (c[3] > 0.5 && chromatic(c)) { accents.add(hex(c)); hues.add(Math.round(oklch(c)[2] / 30) % 12); }
  }

  // ---- surface
  // the surface radius: pills (50%, 9999px) and avatars say nothing about the system's corners
  const radiusPx = [...radii.entries()].filter(([v]) => !/%/.test(v)).map(([v, c]) => [px(v), c])
    .filter(([v]) => v !== null && v < 40).sort((a, b) => b[1] - a[1]);
  const blurs = [...shadows.keys()].map((s) => { const m = s.replace(/(rgba?|oklch)\([^)]+\)/gi, '').match(/-?\d+(\.\d+)?px/g) || []; return m.length >= 3 ? parseFloat(m[2]) : 0; });
  const cardLike = new Set(els.filter(([, cs]) => (px(cs.borderTopLeftRadius) || 0) >= 6 && (cs.boxShadow !== 'none' || (px(cs.borderTopWidth) || 0) >= 1)).map(([el]) => el));
  let nest = 0;
  for (const el of cardLike) { let d = 1; for (let n = el.parentElement; n; n = n.parentElement) if (cardLike.has(n)) d++; nest = Math.max(nest, d); }

  // ---- measure (characters per line) and first-viewport density
  const blocks = [];
  for (const el of document.querySelectorAll('p, li, dd, blockquote, figcaption')) {
    if (!visible(el) || el.querySelector('p, li, div')) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length < 90) continue;
    const l = Math.max(1, lines(el));
    if (l >= 2) blocks.push(Math.round(t.length / l));
  }
  const vp = { words: 0, interactive: 0, textArea: 0 };
  for (const [el] of els) {
    const t = ownText(el); if (!t) continue;
    const r = el.getBoundingClientRect(); if (r.top >= vh || r.bottom <= 0) continue;
    vp.words += t.split(' ').length;
    const rg = document.createRange(); rg.selectNodeContents(el);
    for (const q of rg.getClientRects()) vp.textArea += Math.max(0, Math.min(q.right, vw) - Math.max(q.left, 0)) * Math.max(0, Math.min(q.bottom, vh) - Math.max(q.top, 0));
  }

  // ---- heading rhythm: more space above a heading than below it
  let rhythmBad = 0; const rhythmEx = [];
  for (const h of document.querySelectorAll('h1, h2, h3, h4')) {
    if (!visible(h)) continue;
    const prev = h.previousElementSibling, next = h.nextElementSibling;
    if (!prev || !next || !visible(prev) || !visible(next)) continue;
    const r = h.getBoundingClientRect(), above = r.top - prev.getBoundingClientRect().bottom, below = next.getBoundingClientRect().top - r.bottom;
    if (above > 0 && below > 0 && above <= below) { rhythmBad++; if (rhythmEx.length < 3) rhythmEx.push(`${short(h)}: ${round(above)}px above, ${round(below)}px below`); }
  }

  // ---- interaction: targets, wrapped labels
  const interactive = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button], [tabindex]:not([tabindex="-1"])')]
    .filter((el) => visible(el) && !el.closest('#jgui-picker'));
  let under24 = 0, under44 = 0, wrapped = 0; const smallEx = [], wrapEx = [];
  const rects = interactive.map((el) => el.getBoundingClientRect());
  // WCAG 2.5.8: an undersized target passes when a 24px circle on its centre
  // touches no other target (the spacing exception)
  const crowded = (i) => {
    const r = rects[i], cxp = r.left + r.width / 2, cyp = r.top + r.height / 2;
    return rects.some((o, j) => j !== i && Math.hypot(Math.max(o.left - cxp, 0, cxp - o.right), Math.max(o.top - cyp, 0, cyp - o.bottom)) < 12);
  };
  interactive.forEach((el, i) => {
    const r = rects[i], cs = getComputedStyle(el);
    const inline = el.tagName === 'A' && cs.display === 'inline' && el.parentElement && ownText(el.parentElement);  // a link inside prose: exempt (WCAG 2.5.8)
    if (!inline) {
      if ((r.width < 24 || r.height < 24) && crowded(i)) { under24++; push(smallEx, `${short(el)} ${round(r.width)}×${round(r.height)}`); }
      if (r.width < 44 || r.height < 44) under44++;
    }
    if ((el.tagName === 'BUTTON' || el.closest('nav')) && (el.textContent || '').trim().length > 3 && lines(el) >= 2) { wrapped++; push(wrapEx, short(el)); }
    if (r.top < vh && r.bottom > 0) vp.interactive++;
  });

  // ---- overflow and clipping at this width
  const docW = document.documentElement.scrollWidth, offenders = [];
  if (docW > vw + 1) for (const [el] of els) { const r = el.getBoundingClientRect(); if (r.right > vw + 1 && offenders.length < 5) offenders.push(`${short(el)} +${round(r.right - vw)}px`); }
  let clipped = 0, truncated = 0; const clipEx = [];
  // screen-reader-only text (1px, absolutely placed, clipped) is hidden on purpose, not clipped
  const srOnly = (el, cs) => cs.position === 'absolute' && (el.clientWidth <= 1 || el.clientHeight <= 1 || cs.clip !== 'auto' || /inset\(50%\)/.test(cs.clipPath || ''));
  for (const [el, cs] of els) {
    if (!ownText(el) || srOnly(el, cs)) continue;
    if (['hidden', 'clip'].includes(cs.overflowX) && el.scrollWidth > el.clientWidth + 1) {
      if (cs.textOverflow === 'ellipsis') truncated++; else { clipped++; push(clipEx, short(el)); }
    }
  }

  // ---- stylesheets: reduced motion, hover gating
  let reducedMotionRule = false, ungatedHover = 0, unreadable = 0;
  const walk = (rules, gated) => {
    for (const r of rules) {
      if (r.type === 4) {
        const txt = r.conditionText || (r.media && r.media.mediaText) || '';
        if (/prefers-reduced-motion/.test(txt)) reducedMotionRule = true;
        walk(r.cssRules, gated || /hover\s*:\s*hover/.test(txt));
        continue;
      }
      if (r.type === 1 && /:hover/.test(r.selectorText || '') && r.style && (r.style.transform || r.style.translate || r.style.scale) && !gated) ungatedHover++;
      if (r.cssRules && r.cssRules.length) walk(r.cssRules, gated);
    }
  };
  for (const s of document.styleSheets) { try { walk(s.cssRules, false); } catch (e) { unreadable++; } }

  // ---- mobile specifics
  const meta = (document.querySelector('meta[name=viewport]') || {}).content || null;
  let smallInputs = 0; const inputEx = [];
  if (vw <= 480) {
    for (const el of document.querySelectorAll('input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=hidden]):not([type=submit]):not([type=button]), textarea, select')) {
      if (!visible(el)) continue;
      const f = px(getComputedStyle(el).fontSize);
      if (f < 16) { smallInputs++; push(inputEx, `${short(el)} ${f}px`); }
    }
  }

  // ---- a purple-to-blue gradient behind the first screen
  let gradientHero = false;
  for (const [el, cs] of els) {
    const r = el.getBoundingClientRect();
    if (r.top > vh || r.width * r.height < vw * vh * 0.25 || !/gradient/.test(cs.backgroundImage)) continue;
    const stops = (cs.backgroundImage.match(/(rgba?\([^)]+\)|oklch\([^)]+\)|#[0-9a-f]{3,8})/gi) || []).map(rgba).filter(Boolean).filter(chromatic);
    if (stops.length >= 2 && stops.every((c) => { const h = hsl(c)[0]; return h >= 200 && h <= 330; })) gradientHero = true;
  }

  // ---- census: the elements a judge may cite, by selector
  const census = [];
  const pick = (sel, role) => { for (const el of document.querySelectorAll(sel)) { if (census.length >= 80) return; if (!visible(el) || el.closest('#jgui-picker')) continue; const r = el.getBoundingClientRect(), cs = getComputedStyle(el); census.push({ sel: short(el), role, text: (el.innerText || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 40), rect: [round(r.left), round(r.top + scrollY), round(r.width), round(r.height)], font: `${px(cs.fontSize)}px/${cs.fontWeight}` }); } };
  pick('h1, h2, h3', 'heading'); pick('button, [role=button], a.btn, a[class*=button]', 'action'); pick('input, select, textarea', 'field');
  pick('nav a', 'nav'); pick('img, svg[role=img], video', 'media');
  for (const el of cardLike) { if (census.length >= 80) break; const r = el.getBoundingClientRect(); census.push({ sel: short(el), role: 'surface', text: '', rect: [round(r.left), round(r.top + scrollY), round(r.width), round(r.height)], font: '' }); }

  const failures = [...contrastFail.values()].sort((a, b) => b.count - a.count);
  // the page's real ground: what sits behind the middle of the first screen
  // (a wrapper often paints it, not <body>)
  // sample a grid, climb to the first element at least half the viewport
  // wide, and take the commonest effective background (buttons and cards
  // under a sample point would otherwise decide it)
  const grounds = new Map();
  for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) for (const fy of [0.2, 0.5, 0.8]) {
    let n = document.elementFromPoint(vw * fx, vh * fy);
    while (n && n.parentElement && n.getBoundingClientRect().width < vw * 0.5) n = n.parentElement;
    const g = n && bgOf(n);
    if (g) add(grounds, hex(g));
  }
  const g0 = [...grounds.entries()].sort((a, b) => b[1] - a[1])[0];
  const ground = g0 ? rgba(g0[0]) : base;
  return {
    title: document.title, finalUrl: location.href, viewport: `${vw}x${vh}`,
    rendersDark: lum(ground) < 0.2, pageBg: hex(ground), elementsMeasured: els.length, truncatedAtMax: els.length >= MAX,
    profile: {
      typefaces: families.size, bodyPx: body, dominantWeight: weightsTop[0] ? weightsTop[0][0] : null,
      dominantWeightSharePct: weightsTop[0] && chars ? round(100 * weightsTop[0][1] / chars) : null,
      distinctSizes: sizeChars.size, sizesUsed, scaleRatio: scaleRatio ? round(scaleRatio, 3) : null, nearestScale,
      largestStep: steps.length ? round(Math.max(...steps), 3) : null,
      distinctSpacing: spacing.size, spacingOn4Pct: onGrid(4), spacingOn8Pct: onGrid(8), spacingCarrying90Pct: carry,
      distinctTextColors: textColors.size, accentColors: accents.size, accentHues: hues.size,
      distinctRadii: radii.size, dominantRadiusPx: radiusPx[0] ? radiusPx[0][0] : null,
      distinctShadows: shadows.size, maxShadowBlurPx: blurs.length ? Math.max(...blurs) : 0,
      hasMotion: durations.size > 0 || animations.size > 0, measureCpl: blocks.length ? median(blocks) : null,
      hierarchyLevels: [...pairs.values()].filter((n) => n >= 2).length,
      firstViewport: { words: vp.words, interactive: vp.interactive, textCoveragePct: round(100 * vp.textArea / (vw * vh)) },
      centeredTextPct: chars ? round(100 * centered / chars) : 0, chars,
    },
    type: { families: top(families, 4), sizesByChars: top(sizeChars, 14), sizesByElements: top(sizeEls, 14), runningCopy: top(copyChars, 4),
            weights: top(weightChars, 8), leading: top(leading, 10), tracking: top(trackingT, 8),
            tightLeading, tightExamples: ex.tight, wideTracking, trackingExamples: ex.tracking, headingRhythm: rhythmBad, rhythmExamples: rhythmEx },
    spacing: { scale: top(spacing, 16) },
    color: { text: top(textColors, 10).map((x) => ({ ...x, hex: hex(rgba(x.value) || [0, 0, 0]) })),
             background: top(bgColors, 10).map((x) => ({ ...x, hex: hex(rgba(x.value) || [0, 0, 0]) })), accents: [...accents].slice(0, 12) },
    surface: { radii: top(radii, 8), shadows: top(shadows, 6), borders: top(borders, 6), cardNestingDepth: nest, sideStripes, glowShadows: glow },
    motion: { durations: top(durations, 8), easings: top(easings, 8), animations: top(animations, 6), transitionAll: transAll,
              layoutTransitions: layoutTrans, layoutExamples: ex.layout, easeIn, easeInExamples: ex.easeIn, bounce, bounceExamples: ex.bounce,
              over300ms: slow, slowExamples: ex.slow, reducedMotionRule, ungatedHoverTransforms: ungatedHover, unreadableSheets: unreadable },
    layout: { contentWidths: top(widths, 6), measureCpl: blocks.length ? median(blocks) : null,
              p90Cpl: blocks.length ? [...blocks].sort((a, b) => a - b)[Math.floor(blocks.length * 0.9)] : null, copyBlocks: blocks.length },
    contrast: { checked: contrastChecked, unknown: contrastUnknown, failing: failures.reduce((a, f) => a + f.count, 0), failures: failures.slice(0, 20) },
    targets: { interactive: interactive.length, under24, under44, examples: smallEx, wrappedLabels: wrapped, wrappedExamples: wrapEx },
    overflow: { horizontal: docW > vw + 1, docWidth: docW, offenders, clippedText: clipped, clippedExamples: clipEx, truncatedText: truncated },
    mobile: { viewportMeta: meta, blocksZoom: !!(meta && /user-scalable\s*=\s*(no|0)|maximum-scale\s*=\s*1(\.0)?\b/i.test(meta)), smallInputs, smallInputExamples: inputEx },
    tells: { emojiIcons: ex.emoji, purpleBlueGradientHero: gradientHero, gradientText, emDashes, placeholders: ex.placeholders },
    census,
  };
}

function stressText() {
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('script,style,svg,#jgui-picker') || el.children.length) continue;
    const t = (el.textContent || '').trim();
    if (t.length < 3 || t.length > 60) continue;
    el.textContent = `${t} ${t} ${t}`; n++;
  }
  let clipped = 0;
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    const hidden = cs.position === 'absolute' && (el.clientWidth <= 1 || el.clientHeight <= 1 || cs.clip !== 'auto');
    if (!hidden && ['hidden', 'clip'].includes(cs.overflowX) && cs.textOverflow !== 'ellipsis' && el.scrollWidth > el.clientWidth + 1 && el.textContent.trim()) clipped++;
  }
  return { textsTripled: n, horizontal: document.documentElement.scrollWidth > innerWidth + 1, docWidth: document.documentElement.scrollWidth, clippedText: clipped };
}

// frozen motion for a deterministic shot; the kit's picker bar is chrome for the human, not part of the design
const FREEZE = '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;transition:none!important;caret-color:transparent!important;scroll-behavior:auto!important}#jgui-picker{display:none!important}';

async function focusPass(page, presses = 12) {
  let tabbed = 0, shown = 0; const missing = [];
  await page.evaluate(() => { if (document.activeElement) document.activeElement.blur(); window.scrollTo(0, 0); });
  for (let i = 0; i < presses; i++) {
    await page.keyboard.press('Tab');
    const r = await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body || el.closest('#jgui-picker')) return null;
      const cs = getComputedStyle(el);
      const outline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 1 && cs.outlineColor !== 'rgba(0, 0, 0, 0)';
      const ring = !!cs.boxShadow && cs.boxShadow !== 'none';
      return { visible: outline || ring, name: el.tagName.toLowerCase() + ((el.textContent || '').trim() ? ` "${el.textContent.trim().slice(0, 24)}"` : '') };
    });
    if (!r) continue;
    tabbed++;
    if (r.visible) shown++; else if (missing.length < 5) missing.push(r.name);
  }
  return { tabbed, visibleFocus: shown, missing };
}

// ------------------------------------------------------------------ driver

async function capture(browser, url, vpStr, theme, a) {
  const [w, h] = vpStr.split('x').map(Number);
  const mobile = w <= 480;
  const ctx = await browser.newContext({
    viewport: { width: w, height: h }, deviceScaleFactor: 1, colorScheme: theme,
    isMobile: mobile, hasTouch: mobile, userAgent: mobile ? MOBILE_UA : DESKTOP_UA,
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e.message || e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  try {
    const resp = await page.goto(url, { waitUntil: 'load', timeout: a.timeoutMs });
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await page.evaluate(() => document.fonts && document.fonts.ready).catch(() => {});
    await page.waitForTimeout(a.waitMs);
    if (a.scroll) {
      await page.evaluate(async () => {
        for (let i = 0; i < 20 && innerHeight + scrollY < document.documentElement.scrollHeight - 2; i++) {
          scrollBy(0, Math.round(innerHeight * 0.8)); await new Promise((r) => setTimeout(r, 150));
        }
        scrollTo(0, 0);
      });
      await page.waitForTimeout(500);
    }
    const overlays = await page.evaluate(hideOverlays);
    const m = await page.evaluate(measurePage);
    Object.assign(m, { url, theme, status: resp ? resp.status() : null, overlaysHidden: overlays, measuredAt: new Date().toISOString() });
    const base = path.join(a.out, `${slugOf(url)}-${w}-${theme}`);
    if (a.shots) {
      await page.addStyleTag({ content: FREEZE });
      await page.waitForTimeout(100);
      await page.screenshot({ path: `${base}.png` });
      m.screenshot = `${base}.png`;
      if (a.full) { await page.screenshot({ path: `${base}.full.png`, fullPage: true }); m.screenshotFull = `${base}.full.png`; }
    }
    if (a.focus) m.focus = await focusPass(page).catch((e) => ({ error: String(e).slice(0, 120) }));
    if (a.stress) {
      m.stress = await page.evaluate(stressText);
      if (a.shots) { await page.screenshot({ path: `${base}.stress.png` }); m.stress.screenshot = `${base}.stress.png`; }
    }
    m.consoleErrors = errors.slice(0, 10);
    fs.writeFileSync(`${base}.json`, JSON.stringify(m, null, 2));
    return { url, viewport: vpStr, theme, json: `${base}.json`, png: m.screenshot || null, ok: true };
  } catch (e) {
    return { url, viewport: vpStr, theme, ok: false, error: String(e.message || e).split('\n')[0].slice(0, 300) };
  } finally {
    await ctx.close().catch(() => {});
  }
}

async function main() {
  let a;
  try { a = parseArgs(process.argv.slice(2)); } catch (e) { console.error(e.message); process.exit(1); }
  fs.mkdirSync(a.out, { recursive: true });
  let pw;
  try { pw = loadPlaywright(); } catch (e) {
    console.error('playwright not found — npm install -g playwright && npx playwright install chromium');
    process.exit(4);
  }
  const browser = await pw.chromium.launch({ headless: true });
  let failed = 0;
  try {
    for (const url of a.urls) for (const vp of a.viewports) for (const theme of a.themes) {
      const r = await capture(browser, url, vp, theme, a);
      if (!r.ok) failed++;
      console.log(JSON.stringify(r));
    }
  } finally {
    await browser.close();
  }
  process.exit(failed ? 4 : 0);
}

if (require.main === module) main();

module.exports = { measurePage, hideOverlays, stressText, slugOf };
