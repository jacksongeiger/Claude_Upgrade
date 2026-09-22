# /jg-ui — design brief (decided 2026-09-21, to be built on the Mac)

Everything below is settled. A local session should read this and build; it
does not need the conversation it came from.

## Why the skill exists

Verified against the kit, 2026-09-21: every UI-shaped piece we have
**inspects**, nothing **constructs**.

- `ui-auditor` says so in its own prompt: "Facts about the rendered page
  only; no opinions about the design." It reports clipped text, overflow,
  overlap, unreadable contrast.
- The `persona-judge` rubric has five criteria — findability, feedback,
  recovery, consistency, wording. All behavioural. None about type,
  spacing, hierarchy, colour or density.
- `tokens_extract.py` reads tokens off a **running** page, so a greenfield
  project has nothing to extract from. Nothing authors a scale.
- `inspire` writes `inspiration.md`; `build.md` never opens it.
- `build.md` gives an executor exactly one UI instruction, and it is
  conditional: use the tokens *if* a path was given, never hardcode.

So the kit can say a screen is broken. It cannot say how to make one good.

## The four decisions (human, terminal picker)

| question | answer |
|---|---|
| What should a finished screen look like? | **Depends on the project.** The skill asks the direction once per project and then holds every screen to that one answer. A crypto scanner and a notes app should not look alike. |
| When does it run? | **On demand** (point it at an ugly screen, say make this good) **and automatically as the build runs** (every screen checked as it is made). Explicitly NOT a "settle the look before any UI" step, and NOT a Nightshift night. |
| What happens when a screen misses the bar? | **Never blocks. Always reports.** No acceptance gate, no refusal. |
| Where do components come from? | **Real ones off the shelf** — fetched live from shadcn/ui, Kokonut UI and Bklit. |

### The tension in decision 3, and why it is survivable

Earlier in the same conversation the point was made that everything in this
kit works because a script refuses without an artifact, and that a UI skill
offering only taste gets skipped the way `inspiration.md` is skipped today.
The human chose advisory anyway. Decision 2 is what saves it: because the
check runs **automatically during the build**, the finding is always
produced and recorded even though it never blocks. Route those findings
into `.loop/backlog.yaml` as rows (`source: ui`, like `persona` rows) so
Nightshift can pick them up later. Advisory, but never invisible.

Do not quietly reintroduce a blocking gate. If the build turns out to need
one, raise it with the human first.

## Named resources (human's choice, all verified)

- **Motion** — motion.dev. Real and enormous: `motion` 73.6M downloads/month,
  `framer-motion` 162.7M, both v13.4.0. motion.dev is Framer Motion's
  current name. This is the animation vocabulary.
- **Kokonut UI** — kokonutui.com. Not an npm package; a shadcn-style
  copy-paste registry. Decorative / marketing components.
- **Bklit** — bklit.com. Same shape, not on npm.
- **shadcn/ui** — ui.shadcn.com. App primitives.

## What the tool finder (rdx) turned up

Third-party descriptions, relayed as data, not endorsements.

| slug | tier | their description |
|---|---|---|
| `readydesign-skill` | yellow | "forces AI coding agents to build UI from six official component sources only" — shadcn/ui, Ant Design, Kokonut UI, Motion, Bklit. MIT, github.com/ZethRise/ReadyDesign-Skill. **406 downloads/month.** |
| `ui-registry-mcp` | yellow | MCP server with "live access to 12 shadcn-style component registries". MIT, github.com/mrityunjay-tiwari/ui-registry-mcp. **249 downloads/month.** |
| `pa11y` | yellow | automated accessibility testing. Top a11y hit, 0.93 with full coverage. |
| `axe-core`, `axe-core-playwright` | yellow | the accessibility engine most tools wrap. |
| `argos-ci-playwright` | yellow | visual regression against approved baselines. |

Someone has already built roughly this skill, naming the same libraries the
human did. At 406 downloads a month it is a pattern worth half an hour of
reading, not a dependency worth taking. **Read it first** — it may save a
day or confirm ours must differ.

**Gap in the index:** no WCAG contrast checker scored above threshold; the
query returned only terminal-colouring packages. That check is ours to
write — roughly fifteen lines of relative-luminance maths.

## Reachability — the reason this is a Mac build

Probed from the cloud container, 2026-09-21. Every one refused:

```
motion.dev 000 · kokonutui.com 000 · bklit.com 000
ui.shadcn.com 000 · mobbin.com 000 · godly.website 000
```

"Real components off the shelf" and "read visually impressive websites"
both require live fetching, so neither can be built or tested in a cloud
session. Follow the pattern `/jg-validate` already uses: probe first, write
a `reachable.json`, and say plainly when a source could not be reached
rather than inventing a component from memory.

## Proposed shape (starting point, not settled)

`/jg-ui`, sitting alongside `/jg-ux`, with `/jg-ux` judging behaviour after
the fact and `/jg-ui` handling craft.

- **`direction`** — asks the per-project look once (decision 1), writes it
  where the builder will actually read it. This replaces the "author a
  design system up front" idea the human rejected: lighter, one question,
  but still a recorded artifact. It must author a starting type scale,
  spacing rhythm and colour set, because extraction cannot work on a
  greenfield project.
- **`fix <url or route>`** — the on-demand path. Look at the screen, fetch
  real components that fit, rewrite it, screenshot before and after.
- **`inspire <what>`** — fetch reference sites and extract **numbers**, not
  adjectives: type ratio, spacing unit, how many colours carry meaning,
  information density. "1.25 scale on an 8px rhythm, two accent colours" is
  actionable; "looks premium" is not. Wire its output into `build.md`,
  which is the fix for the hole where `inspiration.md` is never read.
- **`check`** — runs automatically during the build. Deterministic part:
  contrast maths, a grep for hardcoded hex and px in UI files, proof that
  empty / loading / error / long-content states exist. Judged part: a fresh
  agent grades a screenshot on hierarchy, rhythm, alignment, restraint —
  the visual counterpart to `rubric.md`. Both advisory. Both become backlog
  rows.

## Build notes

- Follow the kit's conventions: a command in `commands/`, agents in
  `agents/`, prompt in `pipeline/prompts/`, scripts beside the other
  pipeline scripts, tests in `pipeline/tests/`.
- Nothing installs itself. If `ui-registry-mcp` is wanted, it is a
  human-approved install, never automatic.
- Re-run `./install.sh` after adding the command and any agents; symlinks
  are per file, so a new file is not linked until then.
- A new agent must also be reachable from the repo's own sessions: the
  symlinks in `.claude/agents/` and `.claude/commands/`.

## As built (2026-09-21)

Built on the Mac, branch `feat/jg-ui`. The four decisions hold: the
direction depends on the project; it runs on demand and by itself in the
build; it never blocks and always reports; components are real, off the
shelf. The contract is `pipeline/README.md` (Stage 4b); the numbers are in
CHANGELOG v3.5.

**Followed the proposal.**

- `/jg-ui` sits beside `/jg-ux`: `/jg-ux` judges behaviour, `/jg-ui`
  handles craft. The four proposed sections exist (`direction`, `inspire`,
  `fix`, `check`), plus `components`, `libraries` and `score`.
- Probe first: `ui_sources.py probe` writes `.pipeline/ui/reachable.json`;
  a source that fails is retried once and named, never replaced from memory.
- `inspire` measures numbers (body, scale ratio, spacing grid, accents,
  motion, density) into `inspiration.json`, and `build.md` now receives its
  path: the hole where `inspiration.md` was never read is closed. What to
  take from a site is written by the session that looked at the
  screenshots, never by the script.
- The contrast check the index lacked is ours: `ui_color.py` (WCAG 2
  luminance, alpha compositing, OKLCH), stdlib only.
- Findings become `.loop/backlog.yaml` rows (`dimension: ui`,
  `source: ui`); a clean re-check closes them. `ui_check.py` exits 0
  whatever it finds. No blocking gate was reintroduced.
- Nothing installs itself: `vendor` prints the packages an item needs.
- Kit conventions: command, agent, prompts and scripts beside the others;
  the `.claude/agents/` and `.claude/commands/` symlinks exist.

**Where it moved.**

- **The direction is three candidates on a picker, not one question.** Up
  to four questions (look, accent, theme, avoid) narrow six looks to three
  on distinct axes; `ui_direction.py picker` renders them on one page (keys
  1–3, D for dark); the human picks, and nothing is written without a yes.
  A look is easier to choose seen than described (the Claude Design
  masterclass: reference, don't describe).
- **DESIGN.md adopted** (the google-labs-code/design.md format), beside
  `design-tokens.json` and `tokens.css`: scripts read the JSON, code
  imports the CSS (which also carries shadcn/ui's variable names, so
  vendored components follow it untouched), people and other tools read
  DESIGN.md. `--from` takes a DESIGN.md (a Claude Design export), a URL or a
  local page.
- **`fix` is three variants behind `js/picker.js`, not one rewrite.** One
  piece per run; three variants on named axes in a harness outside
  production, measured side by side; the human picks; the winner is built
  in and measured before and after (Emil Kowalski's prototype pattern).
- **The check has three kinds.** `floor` (durable: contrast, type,
  targets, focus, overflow, motion, phones, states), `direction` (the page
  against the project's own tokens) and `fashion` (dated tells in
  `ui_tells.json`, rung 2, never scored: anti-slop advice goes stale). The
  0–100 score comes from floor and direction only. Beyond the proposed
  contrast, hex/px grep and states, it measures type, spacing, motion,
  focus, phones and long-content stress through `js/ui_measure.cjs`.
- **The judge is 0–3, grounded, rows only.** The proposed four dimensions
  plus fit to the direction, 0–3 each: raters disagree by two points out of
  ten, so a finer number would be false precision. It sees the screenshots
  before any measured finding; a finding that cites no element from the
  page's census is dropped; it never moves the score.
- **A `ui` Nightshift scorer.** Decision 2 stands: `/jg-ui` is not a night.
  But "so Nightshift can pick them up" needs a scoreboard dimension, since
  the loop never picks a row without one. Need `ui` → a cmd scorer,
  `ui_check.py score` (floor and direction, never the judge), with
  `design-tokens.json` pinned so a night cannot move its own target.
- **A component baseline in `/jg-tools`.** Real components need their
  packages and the loop's allowlist denies `npm install`, so
  `tools_plan.py` offers them pinned for ui + javascript (tailwindcss,
  motion, radix-ui, class-variance-authority, cn, clsx, tailwind-merge,
  lucide-react, sonner) for the human to approve. A build vendors only
  items whose packages are installed and asks about the rest.
- **The build authors a direction when none exists.** The proposal had the
  human answer `direction`. A headless build with no direction would hold
  screens to nothing, so the planner runs `ui_direction.py propose` for the
  look that fits the product, writes it into the build worktree and
  records the look in `decisions`. Unattended, `/jg-ui` itself never adopts
  over an existing file.
- **The skill moved into the repo.** `ui-design` lived only in
  `~/.claude/skills/`, invisible to cloud sessions and headless builds; it
  is versioned in `skills/` and `install.sh` links it.

**What the named resources turned out to be.**

- Reachability: the six hosts refused above all return 200 from the Mac
  (godly.website now redirects to recent.design); the probe reached 13/13
  UI hosts.
- shadcn/ui new-york-v4: 471 registry items, which now import `cn` from the
  `cn` package. Kokonut UI: 51, decorative, marketing and AI chat; 36 use
  motion; 35 of 46 components hardcode colours. Bklit (ui.bklit.com): 56,
  charts only; 19 examples overwrite `app/page.tsx`; some need a paid icon
  licence. Vendored without the CLI, a dry run over every item came out
  clean for 152/158, 47/51 and 42/56.
- Motion: the motion rules and tokens (ease-out, UI under 300ms, transform
  and opacity only, springs with bounce 0) come from emilkowalski/skills
  (MIT, commit 85e8e23), with attribution; `motion` 13.4.0 is in the
  baseline.
- `readydesign-skill`: read first, as asked; two patterns taken, not
  installed. `ui-registry-mcp`: not installed. Both, and 21st.dev as a
  default source, are in `DEAD_ENDS.md`.
