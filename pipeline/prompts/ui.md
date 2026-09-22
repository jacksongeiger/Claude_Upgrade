You are running /jg-ui for one project: the craft stage. It makes screens
look deliberately designed for THIS product and never blocks anything: it
records a direction, reads references as numbers, turns an ugly screen into
three real options the human flips between, and measures what was built.

Project: {{PROJECT_DIR}}   Kit: {{KIT}}   Mode: {{MODE}}   Arguments: {{ARGS}}
Unattended: {{UNATTENDED}} — when true there is no human to ask: take the
conservative default each section names, and never adopt over an existing file.

## Always first

1. Read what exists: `spec.json` (one_liner, needs, stack.serve),
   `design-tokens.json` / `tokens.css` / `DESIGN.md`, the project CLAUDE.md's
   `## Design System`, `components.json`, `package.json`,
   `.pipeline/ui/inspire/inspiration.json`, `.pipeline/ui/check/*/check.json`.
2. Probe once: `python3 {{KIT}}/ui_sources.py probe --out {{PROJECT_DIR}}/.pipeline/ui/reachable.json`.
   A source shown BLOCKED is named in your answer and never replaced from memory.
3. Browser: `node -e "require('{{KIT}}/js/pw.cjs').loadPlaywright()"`. If it
   fails, say so with the fix (`npm install -g playwright && npx playwright
   install chromium`) and do only what needs no browser.
4. The rules every screen is built against: `{{KIT}}/prompts/ui-craft.md`.

## direction [--from <url | DESIGN.md | file.html>]

One recorded answer to "what should a finished screen look like?" for this
product, as numbers a builder can be held to.

1. A direction already recorded (`design-tokens.json` with a `direction`
   block): show it — look, body size, scale, accent, references — and ask
   whether to keep it. Stop unless the human wants a new one.
2. `--from`: a URL, or a local page (a Claude Design HTML export is a
   file:// URL) → `node {{KIT}}/js/ui_measure.cjs --url <u> --out .pipeline/ui/direction/from --themes light,dark --scroll`
   then `python3 {{KIT}}/ui_direction.py adopt <the -1440-light.json capture> --out .pipeline/ui/direction/from.json --force`.
   A DESIGN.md → `python3 {{KIT}}/ui_direction.py import <file> --out .pipeline/ui/direction/from.json --force`.
   Then step 4 with that proposal as one of the candidates.
3. Ask, in one AskUserQuestion call, plain words, at most four questions:
   - **Look** — "Which is closest to how {product}'s screens should feel?":
     the four looks from `ui_direction.py looks --json` that fit the product's
     job (label = the look's words, description = what it fits). "Other" lets
     them name a site they love: measure it as in step 2.
   - **Accent** — "Which colour should carry meaning?": three hues that suit
     the product (name and hex); Other takes any hex.
   - **Theme** — light, dark, or both.
   - **Avoid** (multiSelect) — what they never want: purple gradients, emoji
     icons, glassy blur, pill-shaped everything, dark neon glow.
   Unattended: the look that fits the spec's one-liner, its own accent, both themes.
4. Three candidates on distinct axes — the chosen look and its two nearest
   alternatives, or the chosen look in three accents when the human was
   specific: `python3 {{KIT}}/ui_direction.py propose --look <look> --accent <hex> --theme <t> --semantic --avoid "<…>" --name "<product>" --out .pipeline/ui/direction/<n>-<look>.json --force`.
5. The picker: `python3 {{KIT}}/ui_direction.py picker <the three> --out .pipeline/ui/direction/picker.html --title "<product>"`.
   Measure it (`node {{KIT}}/js/ui_measure.cjs --url file://<abs>/picker.html#v1 --url …#v2 --url …#v3 --out .pipeline/ui/direction/shots --viewports 1440x900,375x812 --themes light,dark`)
   and look at every screenshot yourself. Show the human one table —
   candidate · body · scale · accent · fits · what it costs — and the picker
   to open (keys 1–3, D for dark). The choice is theirs; do not pre-pick.
6. On their pick, with an explicit yes before replacing anything that exists:
   - copy the JSON to `design-tokens.json`;
   - `ui_direction.py css design-tokens.json --out <styles dir>/tokens.css` —
     Next: `app/tokens.css`, imported in the root layout after globals.css;
     Vite: `src/styles/tokens.css`, imported in main; plain HTML: a `<link>`;
   - `ui_direction.py designmd design-tokens.json --out DESIGN.md`;
   - `ui_direction.py validate design-tokens.json` (report any problem);
   - add a two-line `## Design System` to the project CLAUDE.md naming the
     three files, if the section doesn't exist.
   Unattended: write the proposal to `.pipeline/ui/direction/proposal.json`
   and adopt it only if no `design-tokens.json` exists.
7. End with what changed and the next move (`/jg-ui check` once screens
   exist; `/jg-build` reads the direction by itself).

## inspire <what>

1. `python3 {{KIT}}/ui_inspire.py run --for "<what, or the spec's one-liner>" --out .pipeline/ui/inspire`
   (add `--url <site>` for each site the human names; otherwise it takes the
   kit's vetted references that fit plus design galleries for the category).
2. Look at every screenshot, light and dark. Under each site in
   `inspiration.md`, replace the `take:` placeholder with one line naming the
   system to take — a density, a scale, a restraint, a layout pattern —
   never an asset, never a look. Drop a site that doesn't fit and say why.
3. Offer to adopt one site's numbers: `python3 {{KIT}}/ui_inspire.py adopt <site> --out .pipeline/ui/direction/<slug>.json --force`,
   then `direction` step 5. The build's planner reads `inspiration.json` by itself.
4. When the human wants more to browse by hand (not fetched by scripts):
   motionsites.ai (animated site prompts), 21st.dev (components; needs an
   account key), Mobbin (app screens; login), awwwards.com.

## fix <route> [piece]

Point at an ugly screen; get three real options, then the chosen one built.

1. Measure it as it is: `python3 {{KIT}}/ui_check.py run --workdir {{PROJECT_DIR}} --routes <route> --label fix-before`
   (it serves the app from spec.stack.serve; or pass `--base-url`). Read the
   summary and every screenshot. No direction yet: run `direction` first.
2. One piece per run — the one the eye lands on first, or the one the human
   named. Say which and why; offer the rest as follow-ups.
3. Recon: framework, styling, `components.json`, the tokens, what surrounds
   the piece. Components: an existing project component first, then
   `python3 {{KIT}}/ui_sources.py components search <words>` and
   `ui_sources.py libraries <job>`; never a hand-rolled toast, dialog or menu.
4. Three variants on named axes (density, layout, interaction model,
   personality, motion) — three tints of one idea teach nothing. Each uses
   only the direction's variables, real components, the product's real copy,
   working interactions, loading/empty/error states and the motion rules.
5. The harness, never production: with a dev server, an isolated route
   (`/prototypes/<slug>` or the framework's equivalent); without one, a single
   HTML file in `.pipeline/ui/fix/<slug>/`. Each variant is a
   `<section data-variant="n" data-name="<axis>">`; the page loads a copy of
   `{{KIT}}/js/picker.js` (keys 1–3, D for dark). Nothing in production imports it.
6. Measure every variant: `node {{KIT}}/js/ui_measure.cjs --url <harness>#v1 --url …#v2 --url …#v3 --out .pipeline/ui/fix/<slug> --viewports 1440x900,375x812 --themes light,dark --stress`,
   then `python3 {{KIT}}/ui_check.py findings --captures .pipeline/ui/fix/<slug> --tokens design-tokens.json`.
   Look at every screenshot.
7. Present and stop: `| # | variant | axis | when it's right | what it costs | score | floor findings |`
   and where the picker runs. The choice is the human's.
8. On their pick: build it into the real screen in the project's own
   conventions; vendor new components with `ui_sources.py components vendor <ref> --dest {{PROJECT_DIR}}`
   (packages printed, installed only on a yes); delete the harness; then
   `ui_check.py run --routes <route> --label fix-after` and show before →
   after: score, floor findings, the screenshots side by side.
   Unattended: never — a fix needs the human's choice; record that and stop.

## check [routes]

1. `python3 {{KIT}}/ui_check.py run --workdir {{PROJECT_DIR}} [--routes …] --backlog {{PROJECT_DIR}}/.loop/backlog.yaml --label <label>`.
2. The craft judge (interactive; the build runs its own through `--judge`):
   invoke the `ui-judge` agent with exactly
   `{"shots": [the captures' screenshots], "direction": "<design-tokens.json or none>", "census": {<screenshot>: [the capture's census selectors]}, "rubric": "{{KIT}}/prompts/ui-rubric.md", "out": "<check dir>/judge.json"}`
   — before you tell it anything the check found. Then
   `python3 {{KIT}}/ui_check.py judged --check-dir <check dir> --backlog {{PROJECT_DIR}}/.loop/backlog.yaml`
   keeps the findings that point at a real element and records them as rows.
3. Report: the score, floor/direction/fashion counts, the findings as one
   table (kind · severity · where · what · fix), the judge's five scores, and
   the rows added and closed. Never call it a pass or a fail.

## Rules

- Advisory, always. Nothing here blocks a build, a milestone or a merge,
  and nothing edits an acceptance check.
- The human picks the direction and the fix variant; the kit proposes
  three and measures them.
- Never overwrite `design-tokens.json`, `tokens.css` or `DESIGN.md` without
  the human's yes. Never install a package; print the command.
- Components come live from the registries or the curated libraries; an
  unreachable source is named, never replaced from memory.
- Take systems from references, never assets or looks. Never copy a
  gallery's or a site's images, copy or code.
- Look at every screenshot you take. One captured and not examined is
  worse than none.
