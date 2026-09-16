# Claude_Upgrade

A personal Claude Code configuration kit: a global `CLAUDE.md`, reusable slash-command prompts, per-stack `CLAUDE.md` templates, and a git pre-push hook. The goal is to make Claude Code behave consistently across projects — concise communication, feasibility-first workflow, clean secrets/dependency hygiene, and disciplined documentation.

## Structure

```
Claude_Upgrade/
  CLAUDE.md              # global instructions for Claude Code
  install.sh             # one-shot setup: symlinks everything into ~/.claude/
  commands/              # slash commands (each file is the full prompt)
    jg-feasibility.md
    jg-review.md
    jg-changelog.md
    jg-status.md
    jg-skeptic.md
  templates/
    CLAUDE.general.md    # generic per-project template
    CLAUDE.python.md     # Python project template
    CLAUDE.nextjs.md     # Next.js project template
  hooks/
    pre-push             # warn before pushing to main
  CHANGELOG.md
  DEAD_ENDS.md
  README.md
```

All command files are prefixed `jg-` so they don't collide with built-in Claude Code commands or skills of the same name (`/review`, `/status`, etc.). Invoke them as `/jg-review`, `/jg-feasibility`, and so on.

## Setup

```bash
git clone <repo-url> ~/Claude_Upgrade
cd ~/Claude_Upgrade
./install.sh
```

`install.sh` symlinks the following into `~/.claude/`:

- `CLAUDE.md` → `~/.claude/CLAUDE.md`
- each `commands/*.md` → `~/.claude/commands/`

Because these are symlinks, any edit you make in the repo is live everywhere immediately — no re-installing. Re-running `install.sh` is safe: it skips links that already point to the right place, replaces stale links, and refuses to overwrite real files.


## Nightshift — the unattended improvement loop

`loop/` is a system that improves a project for hours on a hard cost cap
while you sleep, and cannot touch `main`. Fable plans and reviews; Sonnet
(Opus for hard tasks) executes in isolated git worktrees; a **script, never a
model,** measures the result and decides whether to keep it.

```bash
/jg-loop init      # once per project, with you present: goals, scoreboard, backlog
/jg-loop dryrun    # one supervised iteration at a small cap; proves the cost meter
/jg-loop start --cap 40 --hours 6
/jg-loop status    # or just glance at the statusline
/jg-loop review    # the morning digest, in plain English
```

What you see, always, at the bottom of the terminal:

```
● loop/2026-09-16 · iter 7 · EXEC · score 71.4 ▲2.1 (best 71.4) ▁▂▃▅▆▇ · flat 0/3 · rung 1 · $18.40/$40 ▮▮▮▮▮▮▮▮▮░
```

The one rule everything hangs on: **a row without a scoreboard dimension is
not a candidate.** The loop picks the dimension furthest from target, then the
first row for it; it climbs a ladder (backlog → unfinished threads →
hypotheses → ask you) only when the rung below is empty; flat or regressed
iterations are reset, never kept; and the scorers live outside the worktree
so an executor cannot edit its own judge.

Design and the failure modes it was tested against: `loop/README.md` is the
contract; the design pages are linked from the CHANGELOG.

## Per-project templates

Pick the template that matches your stack and copy it into the project root as `CLAUDE.md`, then fill in the placeholders:

```bash
cp ~/Claude_Upgrade/templates/CLAUDE.python.md /path/to/your/project/CLAUDE.md
cp ~/Claude_Upgrade/templates/CLAUDE.nextjs.md /path/to/your/project/CLAUDE.md
cp ~/Claude_Upgrade/templates/CLAUDE.general.md /path/to/your/project/CLAUDE.md
```

## Pre-push hook

Install the hook into a project with:

```bash
~/Claude_Upgrade/install.sh --project /path/to/your/project
```

This symlinks `hooks/pre-push` into the target project's `.git/hooks/pre-push`, so edits to the hook in this repo flow through to every project that installed it. The hook prompts for confirmation before pushing to `main`. In non-interactive environments (CI, automated tooling) it allows the push through silently — the prompt is a safety net for humans, not an authorization barrier.
