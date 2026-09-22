# Reference sites

Designers have already solved most of this. Rather than invent a type scale,
read one that demonstrably works.

## The vetted set

Pick 1–2 whose *problem* matches yours, not whose look you like.

| Site | Read it for | Closest to |
|---|---|---|
| `linear.app` | dense data, keyboard-first, restraint | dashboards, consoles, internal tools |
| `stripe.com` | marketing pages that stay serious | landing pages, docs, developer sites |
| `vercel.com` | near-monochrome, heavy borders | dashboards, deploy/status UIs |
| `ray.so` / `raycast.com` | dark-first, compact, elevation | launchers, palettes, desktop-feel apps |
| `posthog.com` | dense analytics that stay legible | charts, tables, filters |
| `railway.com` | dark dashboards with real depth | infra dashboards |
| `resend.com` | small surface, no clutter | simple CRUD apps |

Measured status: **Linear and Stripe measured** (2026-08). The rest are vetted
by eye and unmeasured — measure before quoting numbers from them.

## What was measured

Linear, homepage, 1200px wide, with `extract.js` (2026-08):

| | Linear |
|---|---|
| Typefaces | 1 (Inter Variable) + 1 mono |
| Body size | **14px** (31% of text) — 12/13/15 carry the rest |
| Weights | **400 (77%)**, 510 emphasis, 590 rare, 300 |
| Leading | **1.71x at 14px**, 1.5x elsewhere |
| Spacing | 8 (402) · 12 · 32 · 4 · 24 · 6 · 2 · 3 — **26% off a strict 4px grid** |
| Radii | 2 / 4 / 6 / 8 / 12, plus 50% and pill |
| Motion | **0.16s** dominant, 0.1s, `cubic-bezier(.25,.46,.45,.94)` (ease-out quad) |
| Text colors | a 4-step gray ramp carries 477 of ~600 text elements |

**Correction (2026-08):** an earlier ad-hoc script reported 16px body for both
Linear and Stripe. It counted ancestor elements as text, so every wrapper
inherited 16px and swamped the tally. Measured properly, Linear's product-density
body is **14px**. Stripe's marketing pages genuinely do sit at 16px — the two are
different problems, which is the point of picking a reference by problem.

Two consequences for the rules:

- **14–15px is right for dense product UI**, 16px for marketing and prose. Both
  are far above the 10–13px that assembled UIs drift toward.
- **A strict 4px grid is not what good sites do.** Linear is 26% off it. What
  they have is a *short* scale — 8 values carry 90% of usage. Fewer distinct
  values matters more than divisibility by four.

## The convergent findings

Two independent teams, opposite problems, same answers. Treat these as the
default and deviate only with a reason:

1. **One typeface.** A second family is for code, not for contrast.
2. **16px body for marketing and reading; 14–15px for dense product UI.**
   Never the 10–13px assembled UIs drift toward. Density comes from spacing
   and fewer elements, not from shrinking type.
3. **Regular weight carries the page.** Neither site's dominant weight exceeds
   590. Bold is an accent, never the baseline.
4. **Spacing doubles.** Stripe is literally 8/16/32/64. Anything off a 4px grid
   should be rare enough to name a reason for.
5. **Small radii.** 2–8px. Large radii read as consumer-toy, not product.
6. **Few text colors.** A foreground, a muted, and an accent — not a gradient
   of nine grays.

## How to use a reference

Do not copy a look. Extract the *system*, apply it to your own content, then
measure your own build with the same tool and close the gap.

The failure mode is aesthetic mimicry — copying Linear's dark gradient hero onto
a CRUD app. What transfers is the discipline: one family, 16px, regular weight,
doubling scale.
