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
