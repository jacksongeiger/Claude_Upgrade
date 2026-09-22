/**
 * Design-system extractor.
 *
 * Reads the design a page actually shipped: every number comes from
 * getComputedStyle on elements that are really visible, weighted by how many
 * elements use it. Declared-but-unused CSS is invisible to this, which is the
 * point — a stylesheet lies about what a design does, rendered pixels don't.
 *
 * Usage (Playwright MCP):
 *   1. browser_navigate to the page
 *   2. pass this file's contents as the `function` argument to browser_evaluate
 *
 * Works unchanged on a reference site and on your own localhost, which is what
 * makes the two comparable.
 */
() => {
  const MAX_ELEMENTS = 6000;

  // Visible elements only. An off-screen nav or a display:none modal would
  // otherwise contribute values the design never actually shows.
  const els = [];
  const all = document.querySelectorAll('body *');
  for (let i = 0; i < all.length && els.length < MAX_ELEMENTS; i++) {
    const el = all[i];
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    els.push([el, getComputedStyle(el)]);
  }

  const px = (v) => { const n = parseFloat(v); return Number.isFinite(n) ? n : null; };

  // Direct text children only. Using el.textContent counts every ancestor of a
  // paragraph as if it were text, so <body> registers as 16px and the tally is
  // dominated by wrappers.
  const hasText = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3 && n.textContent.trim()) return true;
    }
    return false;
  };

  const tally = (fn) => {
    const m = new Map();
    for (const [el, cs] of els) {
      const v = fn(cs, el);
      if (v === null || v === undefined || v === '') continue;
      for (const x of (Array.isArray(v) ? v : [v])) {
        if (x === null || x === undefined || x === '') continue;
        m.set(x, (m.get(x) || 0) + 1);
      }
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  };

  const top = (rows, n) => rows.slice(0, n).map(([value, count]) => ({ value, count }));
  const total = (rows) => rows.reduce((a, [, c]) => a + c, 0);
  const share = (part, whole) => whole ? Math.round((part / whole) * 100) : 0;

  // ------------------------------------------------------------------ type
  const families = tally((cs) => cs.fontFamily.split(',')[0].replace(/["']/g, '').trim());
  const sizes    = tally((cs, el) => hasText(el) ? px(cs.fontSize) : null);
  const weights  = tally((cs, el) => hasText(el) ? cs.fontWeight : null);
  const leading  = tally((cs, el) => {
    if (!hasText(el)) return null;
    const lh = px(cs.lineHeight), fs = px(cs.fontSize);
    return (lh && fs) ? `${(lh / fs).toFixed(2)}x @ ${fs}px` : null;
  });
  const tracking = tally((cs, el) => {
    if (!hasText(el) || cs.letterSpacing === 'normal') return null;
    return `${cs.letterSpacing} @ ${px(cs.fontSize)}px`;
  });

  // The body size is the size carrying the most characters in reading range —
  // not the most elements, which a 10px label repeated in a table can win.
  const chars = new Map();
  for (const [el, cs] of els) {
    if (!hasText(el)) continue;
    let n = 0;
    for (const t of el.childNodes) if (t.nodeType === 3) n += t.textContent.trim().length;
    const v = px(cs.fontSize);
    if (v >= 11 && v <= 20) chars.set(v, (chars.get(v) || 0) + n);
  }
  const byChars = [...chars.entries()].sort((a, b) => b[1] - a[1])[0];
  const bodySize = byChars ? [byChars[0], (sizes.find(([v]) => v === byChars[0]) || [0, 0])[1]]
    : sizes.find(([v]) => v >= 11 && v <= 20) || sizes[0] || null;
  const textTotal = total(sizes);

  // ------------------------------------------------------------- spacing
  const SPACING_PROPS = [
    'gap', 'rowGap', 'columnGap',
    'paddingTop', 'paddingBottom', 'paddingLeft', 'paddingRight',
    'marginTop', 'marginBottom',
  ];
  const spacing = tally((cs) => {
    const out = [];
    for (const p of SPACING_PROPS) {
      const v = px(cs[p]);
      if (v && v > 0 && v <= 240) out.push(v);
    }
    return out;
  });
  const offGrid = spacing.filter(([v]) => v % 4 !== 0);

  // ------------------------------------------------------------- surface
  const radii   = tally((cs) => cs.borderRadius !== '0px' ? cs.borderRadius : null);
  const shadows = tally((cs) => cs.boxShadow !== 'none' ? cs.boxShadow : null);
  const borders = tally((cs) => {
    const w = px(cs.borderTopWidth);
    return (w && w > 0) ? `${cs.borderTopWidth} ${cs.borderTopColor}` : null;
  });

  // --------------------------------------------------------------- color
  const textColors = tally((cs, el) => hasText(el) ? cs.color : null);
  const bgColors   = tally((cs) => {
    const b = cs.backgroundColor;
    return (b && b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') ? b : null;
  });

  // -------------------------------------------------------------- motion
  const animated = (cs) => {
    const d = (cs.transitionDuration || '').split(',').map((s) => s.trim());
    return d.some((s) => s && s !== '0s');
  };
  const durations = tally((cs) =>
    animated(cs) ? (cs.transitionDuration || '').split(',').map((s) => s.trim()).filter((s) => s && s !== '0s') : null);
  // split on top-level commas only: a cubic-bezier(a, b, c, d) is one easing, not four
  const splitTop = (s) => { const out = []; let d = 0, cur = ''; for (const ch of s || '') { if (ch === '(') d++; if (ch === ')') d--; if (ch === ',' && !d) { out.push(cur.trim()); cur = ''; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; };
  const easings = tally((cs) =>
    animated(cs) ? splitTop(cs.transitionTimingFunction) : null);

  // -------------------------------------------------------------- layout
  const contentWidths = tally((cs) => {
    const w = px(cs.maxWidth);
    return (w && w >= 400 && w <= 2000) ? w : null;
  });

  return {
    meta: {
      url: location.href,
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      elementsMeasured: els.length,
      truncated: els.length >= MAX_ELEMENTS,
    },

    // The one-line summary. Diff this between a reference and your own build
    // before reading anything below it.
    profile: {
      typefaces: families.length,
      bodySize: bodySize ? `${bodySize[0]}px` : null,
      bodySizeShare: bodySize ? `${share(bodySize[1], textTotal)}% of text` : null,
      dominantWeight: weights[0] ? weights[0][0] : null,
      dominantWeightShare: weights[0] ? `${share(weights[0][1], total(weights))}% of text` : null,
      distinctSizes: sizes.length,
      distinctWeights: weights.length,
      distinctSpacing: spacing.length,
      spacingOffFourGrid: `${share(total(offGrid), total(spacing))}%`,
      distinctRadii: radii.length,
      distinctShadows: shadows.length,
      distinctTextColors: textColors.length,
      hasMotion: durations.length > 0,
    },

    type:    { families: top(families, 4), sizes: top(sizes, 14), weights: top(weights, 8),
               leading: top(leading, 8), tracking: top(tracking, 6) },
    spacing: { scale: top(spacing, 16), offGrid: top(offGrid, 10) },
    surface: { radii: top(radii, 8), shadows: top(shadows, 6), borders: top(borders, 6) },
    color:   { text: top(textColors, 10), background: top(bgColors, 10) },
    motion:  { durations: top(durations, 8), easings: top(easings, 6) },
    layout:  { contentWidths: top(contentWidths, 6) },
  };
}
