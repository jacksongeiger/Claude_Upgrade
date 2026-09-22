# UI craft — the rules a screen is built against

Read by the build planner (it pastes the rules that apply into each UI
subtask's goal), by `/jg-ui fix`, and by the `ui-design` skill. Every rule
here is either measured by `ui_check.py` or checked by the `ui-judge`
rubric; nothing is here because it sounds tasteful.

## 1. The system comes first

- The project's direction is `design-tokens.json` (the numbers) with
  `tokens.css` (the variables code imports) and `DESIGN.md` (the same, for
  people and other tools). Use the variables: `var(--primary)`,
  `var(--text-sm)`, `var(--space-4)`, `var(--radius-md)`,
  `var(--duration-fast)`, `var(--ease-out)`. Never a literal colour, pixel
  size or duration in a component. A value the system lacks is added to the
  direction (`/jg-ui direction`), not inlined.
- `tokens.css` also sets shadcn/ui's names (`--background`, `--primary`,
  `--muted-foreground`, `--ring`, `--radius` …), so components pulled from
  shadcn, Kokonut UI and Bklit follow the direction untouched. Import it
  after the framework's globals.
- Brand assets the human supplied (logos, marks, photos) are used as files,
  exactly. Never redraw, recolour or regenerate them.

## 2. Type

| rule | number |
|---|---|
| Body | the direction's base (14px dense product UI, 16px marketing and reading); never under 14 |
| Families | one, plus mono for code |
| Weight | 400 carries the page; 500–600 is emphasis; bold is rare |
| Scale | the direction's 5–7 steps; nothing under 12px |
| Leading | 1.4–1.6 for body, tighter as type grows (1.1–1.2 for display) |
| Tracking | ~0 for body; negative for display (−0.01 to −0.02em); never wider than 0.05em on body |
| Measure | prose capped at 60–75 characters (`max-width: 65ch`) |
| Hierarchy | decide what is read first, second, third; deliver it with size, weight and colour together |

## 3. Space, surface, colour

- Spacing comes from the direction's short scale. Tight inside a group,
  generous between groups. More space above a heading than below it.
- One elevation per surface: no cards inside cards. Borders before shadows;
  at most two shadow levels, blur under 24px.
- Colour is chosen by role, never by eye: text, text-muted, accent. The
  accent (primary) is the only saturated colour and appears rarely. Dark
  mode is its own scale, never an inversion. Contrast is 4.5:1 for text,
  3:1 for large text, focus rings and control borders.

## 4. Motion (Motion from motion.dev, or CSS)

From Emil Kowalski's animation standards and Apple's fluid-interface
principles (github.com/emilkowalski/skills, MIT):

- Should it animate at all? Keyboard-driven and 100-times-a-day actions get
  none. Motion needs a purpose: spatial consistency, state, feedback.
- Enter and exit with ease-out (`var(--ease-out)`, cubic-bezier(0.23, 1,
  0.32, 1)); on-screen movement with ease-in-out; never ease-in on UI.
- UI motion under 300ms: presses 100–160, tooltips 125–200, dropdowns
  150–250, modals and drawers 200–300.
- Animate `transform` and `opacity` only; never `transition: all`.
- Nothing appears from `scale(0)`: start at `scale(0.95)` with opacity 0.
  Popovers scale from their trigger (`transform-origin`), modals from centre.
- Press feedback on `:active` (`scale(0.97)`), not only on click.
- Springs (`{type: "spring", bounce: 0, duration: 0.4}` in Motion) for
  anything a user can grab; bounce only after a gesture carried momentum.
  Interruptible: rapidly re-triggered UI uses transitions, not keyframes.
- Stagger groups 30–80ms, never blocking input.
- `prefers-reduced-motion`: keep opacity and colour changes, drop movement.
  Hover motion only under `@media (hover: hover) and (pointer: fine)`.

## 5. Interaction and states

- Every focusable element shows a `:focus-visible` ring
  (`outline: 2px solid var(--ring)`).
- Targets at least 24×24px (44 on touch), or spaced so they don't crowd.
- Every list and every fetch has a loading, an empty and an error state.
  The empty state names the next action; the error says how to fix it.
- Long names, big numbers and translated labels wrap or truncate with an
  ellipsis; they never push the layout sideways. Numbers use
  `font-variant-numeric: tabular-nums`.
- Labels are specific ("Invoices", not "Home"); forms use real labels, not
  placeholders as labels, and validate inline.

## 6. Phones

- `<meta name="viewport" content="width=device-width, initial-scale=1">` —
  never `user-scalable=no` or `maximum-scale=1`.
- Inputs at 16px, or iOS zooms the page.
- `100dvh` for app shells, `100svh` for heroes, never `100vh`/`h-screen`.
- Nothing wider than the viewport at 375px.

## 7. Real components, not hand-rolled ones

Find them with `python3 <kit>/pipeline/ui_sources.py components search <words>`
and the job's library with `ui_sources.py libraries <job>`:

| job | take |
|---|---|
| dialogs, menus, forms, tables, sidebars | shadcn/ui (new-york-v4) |
| marketing, showcase, AI-chat pieces | Kokonut UI (theme it: most items hardcode colours) |
| charts | Bklit, or recharts; Liveline for streaming |
| toasts / command menu / drawers | Sonner / cmdk / Vaul |
| animated numbers | NumberFlow |

`ui_sources.py components vendor <ref> --dest <project>` writes the files
(never over existing ones) and prints the packages the item needs. In a
build, only vendor items whose packages the approved tooling plan already
installed; name any missing package as a question for the human.

The registries are React + Tailwind. Another stack keeps the same tokens and
takes the same components another way: Vue and Svelte use their own shadcn
ports (shadcn-vue, shadcn-svelte); plain HTML, server templates (Jinja,
ERB, Blade) and HTMX take a registry item as the reference implementation —
its structure, states and variants — rewritten against `tokens.css`. Never
add React to an app that doesn't use it.

## 8. Tells to avoid (dated; see `ui_tells.json`)

Purple-to-blue gradient heroes, gradient text, emoji as icons, centred
everything, Tailwind indigo as the accent, glowing coloured shadows, thick
left stripes on cards, uniform emphasis, generic copy ("Welcome to your
dashboard"), placeholder names and invented stats. Fashion changes; the
project's own direction and sections 1–7 do not.
