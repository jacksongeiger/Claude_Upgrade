---
description: Stage 4b — craft. A design direction recorded per project, real components off the shelf, references read as numbers, an ugly screen turned into three options you flip between, and a measured check the build runs by itself. Advisory: never a gate.
---

Kit: `~/Claude_Upgrade/pipeline/` (`KIT`). Argument: `$ARGUMENTS`. Follow
`$KIT/prompts/ui.md` in this session, the section the argument names, with
PROJECT_DIR = the current directory, KIT as above and UNATTENDED = false.

| Argument | Do this |
|---|---|
| *(empty)* | Status in five lines: the direction (look, body, scale, accent — or "none recorded"); the last check's score and date (`.pipeline/ui/check/*/check.json`); open `ui` backlog rows by kind; whether `inspiration.json` exists; whether Playwright loads. Then the one next move: no direction → `direction`; screens but no check → `check`; floor majors → `fix <worst route>`. |
| `direction [--from <url \| DESIGN.md \| page.html>]` | Section `direction`: three looks that fit the product, on one picker page; you choose; tokens.css, design-tokens.json and DESIGN.md written on your yes. `--from` starts from a site you love, a Claude Design export, or any DESIGN.md. |
| `inspire <what>` | Section `inspire`: well-designed sites for this kind of product, measured into numbers (body, scale, spacing grid, accents, motion, density); what to take written per site. |
| `fix <route> [piece]` | Section `fix`: the screen measured, one piece rebuilt three ways on named axes with real components, measured side by side behind a picker; you choose; the winner built in; before/after numbers. |
| `check [routes…]` | Section `check`: the measured check (contrast, type, spacing, motion, focus, phones, states, tokens, dated tells) plus the craft judge; findings become backlog rows. |
| `components <words…>` | `python3 $KIT/ui_sources.py components search <words>`; `components show <ref>` for a pick; `components vendor <ref> --dest .` only on your yes (packages printed, never installed). |
| `libraries <job>` | `python3 $KIT/ui_sources.py libraries <job>` — the one library for toasts, charts, drawers, drag and drop … |
| `score` | `python3 $KIT/ui_check.py score --workdir .`; print the last line. |

## Rules

- Advisory, always: nothing here blocks a build, a milestone or a merge.
  Findings are backlog rows (`dimension: ui`) that Nightshift can pick up.
- You pick the direction and the fix variant; the kit proposes three and
  measures them. Never overwrite design-tokens.json, tokens.css or
  DESIGN.md without your yes; never install a package.
- Components come live from shadcn/ui, Kokonut UI and Bklit, or the curated
  libraries; a source that can't be reached is named, never faked.
- From references take systems — numbers — never assets or looks.
- `/jg-build` uses all of this by itself: the planner reads the direction
  and the inspiration median, and after each milestone the driver runs the
  check (and the judge while budget allows) and records the rows.
