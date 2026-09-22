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
