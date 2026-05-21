# Changelog

---
### v1.0 — 2026-05-20 23:30 PDT
**What changed:** Initial release of the Claude_Upgrade config kit. Includes: global `CLAUDE.md` defining communication style, feasibility-first workflow, code style, secrets/dependency hygiene, git/docs/judgment rules, and macOS environment notes; five self-contained slash commands (`/feasibility`, `/skeptic`, `/review`, `/changelog`, `/status`) with prompts inlined directly into the command files; three per-stack `CLAUDE.md` templates (`general`, `python`, `nextjs`); a `pre-push` git hook that warns before pushing to `main`; and an `install.sh` that symlinks `CLAUDE.md`, the `commands/` directory, and (when present) the `skills/` directory into `~/.claude/`.
**Why:** Manual copy-based install was friction that guaranteed the kit would rot across machines. Separate command-and-skill file pairs added an indirection layer that broke silently when the two were not co-located. Both fixed at v1.0 so the kit ships in a runnable state.
**Result:** One `git clone` + `./install.sh` brings up a consistent Claude Code environment on any machine. Symlinks (not copies) mean edits in the repo are live everywhere immediately. Re-running `install.sh` is safe — it skips already-correct links, replaces stale ones, and refuses to overwrite real files.
**Performance:** N/A
**Breaking changes:** None (initial release)
**Dependencies:** None
---
