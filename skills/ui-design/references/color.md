# Color

Never pick a color. Pick a hue, then let the *role* choose the step.

## The step table

Radix Colors ships 12-step scales where every step has a fixed job. This is the
whole idea — it converts an aesthetic judgment into a lookup.

| Step | Role |
|---|---|
| 1 | App background |
| 2 | Subtle background |
| 3 | Component background |
| 4 | Component background, hovered |
| 5 | Component background, active / selected |
| 6 | Subtle border, separators |
| 7 | Component border, focus ring |
| 8 | Component border, hovered |
| 9 | **Solid fill** — the accent. Primary buttons, filled badges |
| 10 | Solid fill, hovered |
| 11 | **Low-contrast text** — secondary, muted, placeholders |
| 12 | **High-contrast text** — body copy, headings |

Three consequences worth internalizing:

- **Step 9 is the only saturated one.** It is the brand color and it appears
  rarely. If the accent is everywhere, nothing is accented.
- **Step 11 on step 2 clears 4.5:1 by construction.** Contrast stops being
  something to check and becomes something guaranteed.
- **Step 12 is not black.** Pure black on white is harsher than any real product
  uses.

## Dark mode

Use the dark scale, do not invert the light one. The steps keep the same
meanings, so every rule above still holds and no component needs a branch.
Inverting produces glowing whites and muddy accents.

## How many scales

- **One neutral** (gray / slate / sand — pick by whether the UI leans cool or warm)
- **One accent** (the brand hue)
- **Semantic only if used**: red for destructive, amber for warning, green for
  success. Do not define what you will not use.

That is 2–4 scales. `distinctTextColors` should land at 3–5: step 12, step 11,
the accent, and possibly one semantic.

## Without the package

The table is the useful part; the package is optional. Applied to any generated
scale, the role mapping still works. What matters is that a color is chosen by
answering "what is this element?" and never by answering "what looks right?"

Source: `github.com/radix-ui/colors` (MIT). Scales are plain CSS custom
properties — copy the two you need rather than adding a dependency.
