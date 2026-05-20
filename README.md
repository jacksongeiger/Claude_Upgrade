# Claude_Upgrade

A personal Claude Code configuration kit: a global `CLAUDE.md`, reusable slash-command prompts, per-stack `CLAUDE.md` templates, and git hooks. The goal is to make Claude Code behave consistently across projects — concise communication, feasibility-first workflow, clean secrets/dependency hygiene, and disciplined documentation.

## Structure

```
Claude_Upgrade/
  CLAUDE.md              # global instructions for Claude Code
  templates/
    CLAUDE.general.md    # generic per-project template
    CLAUDE.python.md     # Python project template
    CLAUDE.nextjs.md     # Next.js project template
  commands/
    feasibility.md       # /feasibility — pre-build feasibility investigation
    review.md            # /review — codebase audit
    changelog.md         # /changelog — update CHANGELOG.md
    status.md            # /status — quick project state summary
  hooks/
    pre-push             # warn before pushing to main
  README.md
```

## Usage

### Global CLAUDE.md
Copy `CLAUDE.md` to `~/CLAUDE.md` (or merge into your existing one) so Claude Code picks it up across every project.

### Per-project CLAUDE.md
Pick the template that matches your stack and copy it into the project root as `CLAUDE.md`, then fill in the placeholders:

```bash
cp templates/CLAUDE.python.md /path/to/your/project/CLAUDE.md
cp templates/CLAUDE.nextjs.md /path/to/your/project/CLAUDE.md
cp templates/CLAUDE.general.md /path/to/your/project/CLAUDE.md
```

### Slash commands
Drop the `commands/*.md` files into `~/.claude/commands/` (global) or `<project>/.claude/commands/` (per-project) so Claude Code exposes them as `/feasibility`, `/review`, `/changelog`, `/status`.

### Pre-push hook
The `hooks/pre-push` script prompts for confirmation before pushing to `main`. To activate it in any project repo:

```bash
cp hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

Run that command from inside the project repo (not from inside `Claude_Upgrade`). Git hooks are per-repo and not tracked by git, so this step has to be repeated for each project that wants the protection.
