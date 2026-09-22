#!/usr/bin/env node
// persona_driver.cjs — runs a persona's action script against a page with
// Playwright and records the trail (visible state + a screenshot) after
// every action, so a judge with no browser can grade what the user saw.
//
// Playwright is loaded through pw.cjs, which does exactly:
//   require('playwright')  ->  on failure  ->
//   require(path.join(execSync('npm root -g').toString().trim(), 'playwright'))
//
// Two modes:
//
//   Persistent (stdin):
//     node persona_driver.cjs --run <run_dir> --url <start url>
//                              [--viewport 1280x800] [--headless]
//     Reads one JSON action per line from stdin and, after each, prints one
//     JSON line and appends the same object to <run_dir>/trail.json (an
//     array). Stdin EOF without a `done` action writes
//     <run_dir>/result.json {"status":"abandoned","steps":N} and exits 0.
//
//   One-shot (what a Bash-per-step agent actually uses):
//     node persona_driver.cjs --run <run_dir> --url <u> --actions '<json array>'
//     Replays the given actions in order from a freshly launched browser and
//     prints the same lines. Nothing about the previous call is kept — the
//     driver process, browser and page are all new — so the agent passes the
//     WHOLE growing action list each time it wants to take one more step,
//     and <run_dir>/trail.json is overwritten (not appended) with the full
//     replay. If the list's last action is `done`, result.json is written;
//     otherwise the run is left open for the next one-shot call.
//
// Actions (one JSON object per line / array entry):
//   {"action":"goto","url":".."}
//   {"action":"click","target":"<css selector or visible text>"}
//   {"action":"fill","target":"..","value":".."}
//   {"action":"press","key":"Enter"}
//   {"action":"read"}
//   {"action":"done","status":"complete|stuck"}
// `click`/`fill` resolve `target` in this order: a CSS selector, then
// for fill: label → placeholder → textbox name → text; for click: button → link → text → label.
//
// Every printed/trailed line:
//   {"step":n,"action":"..","ok":bool,"error":"..|null","url":"..","title":"..",
//    "visible_text":"<first 1500 chars of innerText>",
//    "clickables":[{"text":"..","role":".."}, ...up to 40],
//    "shot":"<run_dir>/shots/step-n.png"}
// An error on one action is reported in that line, never fatal to the run.
//
// --url is loaded once, silently, before the first action is processed (so a
// script whose first move is `click`/`read` still has a page to act on); that
// bootstrap load is not itself counted as a step or written to the trail.
//
// Exit codes: 0 on a normal end (done, or stdin EOF/abandoned). A driver-level
// failure (Playwright won't launch, bad --actions JSON, bad args) prints to
// stderr and exits 1.

'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { loadPlaywright } = require('./pw.cjs');

function parseArgs(argv) {
  const args = { viewport: '1280x800', headless: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const next = () => argv[++i];
    switch (a) {
      case '--run':
        args.run = next();
        break;
      case '--url':
        args.url = next();
        break;
      case '--viewport':
        args.viewport = next();
        break;
      case '--headless':
        args.headless = true;
        break;
      case '--actions':
        args.actions = next();
        break;
      default:
        throw new Error(`unknown argument: ${a}`);
    }
  }
  if (!args.run) throw new Error('--run is required');
  if (!args.url) throw new Error('--url is required');
  return args;
}

function parseViewport(v) {
  const m = /^(\d+)x(\d+)$/.exec(v || '');
  if (!m) throw new Error(`--viewport must look like 1280x800, got ${v}`);
  return { width: parseInt(m[1], 10), height: parseInt(m[2], 10) };
}

async function collectClickables(page) {
  try {
    return await page.evaluate(() => {
      function role(el) {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit;
        const tag = el.tagName.toLowerCase();
        if (tag === 'button') return 'button';
        if (tag === 'a') return 'link';
        if (tag === 'select') return 'combobox';
        if (tag === 'textarea') return 'textbox';
        if (tag === 'input') {
          const type = (el.getAttribute('type') || 'text').toLowerCase();
          if (['button', 'submit', 'reset'].includes(type)) return 'button';
          if (type === 'checkbox') return 'checkbox';
          if (type === 'radio') return 'radio';
          return 'textbox';
        }
        return tag;
      }
      function visible(el) {
        const r = el.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) return false;
        const style = window.getComputedStyle(el);
        return style.visibility !== 'hidden' && style.display !== 'none';
      }
      const sel = 'button, a, input, select, textarea, [role="button"], [onclick]';
      const els = Array.from(document.querySelectorAll(sel));
      const out = [];
      for (const el of els) {
        if (!visible(el)) continue;
        let text = (el.innerText || el.value || el.getAttribute('aria-label') ||
          el.getAttribute('placeholder') || '').trim();
        text = text.replace(/\s+/g, ' ').slice(0, 120);
        out.push({ text, role: role(el) });
        if (out.length >= 40) break;
      }
      return out;
    });
  } catch (_) {
    return [];
  }
}

// Resolve a human target ("New note", "Title") the way a person reads the
// page: a typing target is a field (label, placeholder, textbox name) before
// it is a word on the page; a click target is a control before it is text.
async function firstOf(candidates) {
  for (const make of candidates) {
    try {
      const loc = make();
      if ((await loc.count()) > 0) return loc.first();
    } catch (_) {
      // not applicable — next candidate
    }
  }
  return null;
}

// A plain word ("Title", "New note") is never a CSS selector, even when it
// happens to be a tag name: `Title` would match the document's <title>.
function looksLikeCss(target) {
  // plain prose: letters, spaces, light punctuation, a dot only at the end
  if (/^[A-Za-z][A-Za-z0-9 ,'’!?-]*[.!?]?$/.test(target)) return false;
  return /^[#.\[]|[>:\[\]=~*]|^\w+(\.|#)\w/.test(target);
}

async function resolveLocator(page, target, intent) {
  const css = () => (looksLikeCss(target) ? page.locator(target) : page.locator('__no_such_selector__'));
  const label = () => page.getByLabel(target, { exact: false });
  const placeholder = () => page.getByPlaceholder(target, { exact: false });
  const textbox = () => page.getByRole('textbox', { name: target, exact: false });
  const button = () => page.getByRole('button', { name: target, exact: false });
  const link = () => page.getByRole('link', { name: target, exact: false });
  const text = () => page.getByText(target, { exact: false });
  const order = intent === 'fill'
    ? [css, label, placeholder, textbox, text]
    : [css, button, link, text, label];
  const loc = await firstOf(order);
  return loc || page.getByText(target, { exact: false }).first();
}

async function doAction(page, action) {
  switch (action.action) {
    case 'goto':
      await page.goto(action.url, { waitUntil: 'load', timeout: 15000 });
      return;
    case 'click': {
      const loc = await resolveLocator(page, action.target, 'click');
      await loc.click({ timeout: 5000 });
      return;
    }
    case 'fill': {
      const loc = await resolveLocator(page, action.target, 'fill');
      await loc.fill(action.value === undefined || action.value === null ? '' : String(action.value),
        { timeout: 5000 });
      return;
    }
    case 'press':
      await page.keyboard.press(action.key);
      return;
    case 'read':
    case 'done':
      return; // no-op: the line printed afterwards is the point
    default:
      throw new Error(`unknown action: ${action.action}`);
  }
}

async function captureState(page, run, step) {
  // Look when a person would: once the network has been quiet for 500 ms, capped at 3 s so a slow page still
  // shows its loading state. At the bare load event, client-fetched screens were still skeletons, and personas
  // clicked loading placeholders that were gone a moment later (memescout, 2026-09-22).
  await page.waitForLoadState('networkidle', { timeout: 3000 }).catch(() => {});
  const shotsDir = path.join(run, 'shots');
  fs.mkdirSync(shotsDir, { recursive: true });
  const shot = path.join(shotsDir, `step-${step}.png`);
  let url = '';
  let title = '';
  let visible_text = '';
  try {
    url = page.url();
  } catch (_) {
    // ignore
  }
  try {
    title = await page.title();
  } catch (_) {
    // ignore
  }
  try {
    visible_text = await page.evaluate(() => (document.body ? document.body.innerText : ''));
    visible_text = (visible_text || '').slice(0, 1500);
  } catch (_) {
    // ignore
  }
  const clickables = await collectClickables(page);
  try {
    await page.screenshot({ path: shot });
  } catch (_) {
    // ignore — a screenshot failure shouldn't sink the step
  }
  return { url, title, visible_text, clickables, shot };
}

async function runStep(page, run, step, action) {
  let ok = true;
  let error = null;
  try {
    await doAction(page, action);
  } catch (err) {
    ok = false;
    error = String((err && err.message) || err);
  }
  const state = await captureState(page, run, step);
  return { step, action: action && action.action, ok, error, ...state };
}

function writeTrail(run, lines) {
  fs.writeFileSync(path.join(run, 'trail.json'), JSON.stringify(lines, null, 2));
}

function appendTrail(run, line) {
  const trailPath = path.join(run, 'trail.json');
  let trail = [];
  if (fs.existsSync(trailPath)) {
    try {
      trail = JSON.parse(fs.readFileSync(trailPath, 'utf8'));
    } catch (_) {
      trail = [];
    }
  }
  trail.push(line);
  writeTrail(run, trail);
}

function writeResult(run, status, steps) {
  fs.writeFileSync(path.join(run, 'result.json'), JSON.stringify({ status, steps }, null, 2));
}

function doneStatus(action) {
  return action.status === 'stuck' ? 'stuck' : 'complete';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  fs.mkdirSync(args.run, { recursive: true });

  const pw = loadPlaywright();
  const browser = await pw.chromium.launch({ headless: args.headless });
  try {
    const context = await browser.newContext({ viewport: parseViewport(args.viewport) });
    const page = await context.newPage();
    try {
      await page.goto(args.url, { waitUntil: 'load', timeout: 15000 });
    } catch (_) {
      // the start page may not load; the first real action's line reports it
    }

    let step = 0;
    let finalStatus = null;

    if (args.actions !== undefined) {
      // one-shot: replay the whole list against a fresh browser/page.
      let actions;
      try {
        actions = JSON.parse(args.actions);
      } catch (err) {
        throw new Error(`--actions must be a JSON array: ${err.message}`);
      }
      const lines = [];
      let userSteps = 0;
      for (const action of actions) {
        step += 1;
        const line = await runStep(page, args.run, step, action);
        if (action && action.setup) line.setup = true; else userSteps += 1;
        console.log(JSON.stringify(line));
        lines.push(line);
        if (action && action.action === 'done') {
          finalStatus = doneStatus(action);
          break;
        }
      }
      writeTrail(args.run, lines);
      // `done` itself is not a step
      if (finalStatus) writeResult(args.run, finalStatus, Math.max(0, userSteps - 1));
    } else {
      // persistent: one action per stdin line.
      writeTrail(args.run, []);
      const rl = readline.createInterface({ input: process.stdin, terminal: false });
      for await (const raw of rl) {
        const text = raw.trim();
        if (!text) continue;
        let action;
        try {
          action = JSON.parse(text);
        } catch (err) {
          step += 1;
          const errLine = {
            step, action: null, ok: false, error: `invalid JSON: ${err.message}`,
            url: page.url(), title: await page.title().catch(() => ''),
            visible_text: '', clickables: [], shot: null,
          };
          console.log(JSON.stringify(errLine));
          appendTrail(args.run, errLine);
          continue;
        }
        step += 1;
        const line = await runStep(page, args.run, step, action);
        if (action && action.setup) line.setup = true;
        console.log(JSON.stringify(line));
        appendTrail(args.run, line);
        if (action && action.action === 'done') {
          finalStatus = doneStatus(action);
          break;
        }
      }
      if (!finalStatus) writeResult(args.run, 'abandoned', step);
    }
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error(String((err && err.stack) || err));
  process.exit(1);
});
