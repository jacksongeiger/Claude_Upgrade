# Claude_Upgrade

A personal Claude Code configuration kit: a global `CLAUDE.md`, reusable slash-command prompts, per-stack `CLAUDE.md` templates, and a git pre-push hook. The goal is to make Claude Code behave consistently across projects — concise communication, feasibility-first workflow, clean secrets/dependency hygiene, and disciplined documentation.

## Structure

```
Claude_Upgrade/
  CLAUDE.md              # global instructions for Claude Code
  install.sh             # one-shot setup: symlinks everything into ~/.claude/
  commands/              # slash commands (each file is the full prompt)
    feasibility.md
    review.md
    changelog.md
    status.md
    skeptic.md
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

## Setup

```bash
git clone <repo-url> ~/Claude_Upgrade
cd ~/Claude_Upgrade
./install.sh
```

`install.sh` symlinks the following into `~/.claude/`:

- `CLAUDE.md` → `~/.claude/CLAUDE.md`
- each `commands/*.md` → `~/.claude/commands/`
- `skills/` → `~/.claude/skills/` (only if the folder is present in the repo)

Because these are symlinks, any edit you make in the repo is live everywhere immediately — no re-installing. Re-running `install.sh` is safe: it skips links that already point to the right place, replaces stale links, and refuses to overwrite real files.

## Per-project templates

Pick the template that matches your stack and copy it into the project root as `CLAUDE.md`, then fill in the placeholders:

```bash
cp ~/Claude_Upgrade/templates/CLAUDE.python.md /path/to/your/project/CLAUDE.md
cp ~/Claude_Upgrade/templates/CLAUDE.nextjs.md /path/to/your/project/CLAUDE.md
cp ~/Claude_Upgrade/templates/CLAUDE.general.md /path/to/your/project/CLAUDE.md
```

## Pre-push hook

The `hooks/pre-push` script prompts for confirmation before pushing to `main`. Git hooks are per-repo and not tracked by git, so install it explicitly in each project you want protected:

```bash
cp ~/Claude_Upgrade/hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

Run that from inside the target project, not from inside `Claude_Upgrade`.
