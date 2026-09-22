---
name: ui-design
description: Use before writing or changing ANY user interface — components, pages, layouts, CSS, Tailwind, colors, spacing, typography, dark mode, responsive behavior, animation, empty states — and whenever a UI needs to look better, more polished, or less AI-generated. Covers finding or authoring the project's design direction, building with real components, and measuring the result instead of guessing at it. The full flow is /jg-ui.
---

# UI design

Taste is not a feeling here. It is: **find the project's direction, build
against it with real components, measure what rendered, close the gap.**
Screenshots hide the problems — a console that looked fine measured 10–13px
text at weight 600 with 18 off-grid spacing values, and nobody saw it by
looking. The kit (`~/Claude_Upgrade/pipeline/`, `KIT`) does the measuring;
`/jg-ui` runs the whole flow.

## 1. Find the system

In order: `design-tokens.json` + `tokens.css` + `DESIGN.md` (the direction
`/jg-ui` records), `tailwind.config.*`, `theme.css` / `globals.css`, a
`## Design System` in the project CLAUDE.md. Use those variables. Never
hardcode a colour, size or duration; if something is missing, add it to the
direction, never inline a one-off.

**No system yet?** `/jg-ui direction` shows three looks that fit the product
on one picker page and writes the one the human picks. Headless:
`python3 $KIT/ui_direction.py propose --look <look> --out design-tokens.json --css <styles>/tokens.css --designmd DESIGN.md`
(`ui_direction.py looks` lists them). From a site the human loves or a
Claude Design export: `/jg-ui direction --from <url | DESIGN.md | page.html>`.

The generated `tokens.css` also sets shadcn/ui's variables, so components
pulled from shadcn, Kokonut UI and Bklit follow the direction untouched.

## 2. Build against it

The rules: `$KIT/prompts/ui-craft.md` — type, spacing, colour roles, motion
(ease-out, under 300ms, transform and opacity only, springs without bounce),
focus, targets, states, phones. Every rule there is measured.

Real components, never hand-rolled: `python3 $KIT/ui_sources.py components search <words>`,
`ui_sources.py libraries <job>` (Sonner, cmdk, Vaul, Motion …), and
`ui_sources.py components vendor <ref> --dest .` to write one in (packages
printed, never installed without a yes).

## 3. Measure your own build

`/jg-ui check` does it all. By hand:

```
node $KIT/js/ui_measure.cjs --url http://localhost:3000/ --out /tmp/ui --viewports 1440x900,375x812 --themes light,dark --stress
python3 $KIT/ui_check.py findings --captures /tmp/ui --tokens design-tokens.json
```

No Node Playwright? Navigate with the Playwright MCP and pass
`references/extract.js` to `browser_evaluate`; read `profile` first.

## 4. Compare with work that's good

`/jg-ui inspire <what the product is>` measures reference sites into numbers
(body size, scale ratio, spacing grid, accents, motion, density). Take a
system — never a look, never an asset. `references/reference-sites.md` has
the vetted set and what each is good for.

## 5. Close the gap, then verify

Fix the largest gap first (body size and weight dominate perceived quality).
An ugly screen: `/jg-ui fix <route>` builds three options on named axes behind
a picker, you choose, the winner is built in, before/after measured.

Then a real browser: 1440×900, 768×1024, 375×667; light **and** dark; tab
through for focus; console clean; an accessibility audit. Look at every
screenshot. Report numbers, not "done": *"16px/400 body on the direction's
scale, spacing 96% on grid, check score 94, no console errors, a11y 97."*

## References

| File | Read when |
|---|---|
| `$KIT/prompts/ui-craft.md` | before building any screen |
| `references/color.md` | choosing or fixing colour (role picks the step) |
| `references/reference-sites.md` | picking a reference by problem |
| `references/extract.js` | measuring without Node Playwright |
| `$KIT/ui_tells.json` | the dated list of generated-UI tells |
