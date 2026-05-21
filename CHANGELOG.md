# Changelog

---
### v1.1 — 2026-05-20 23:45 PDT
**What changed:** Renamed all five command files with a `jg-` prefix (`/jg-feasibility`, `/jg-review`, `/jg-changelog`, `/jg-status`, `/jg-skeptic`) to avoid collision with built-in Claude Code commands and superpowers skills of the same name. Removed the now-dead `skills/` references from `install.sh` and `README.md` (the folder was deleted in v1.0 when commands and skills were collapsed, but the references survived). Replaced the manual `cp hooks/pre-push .git/hooks/pre-push` workflow with `install.sh --project /path/to/repo`, which symlinks the hook into the target project so edits in this repo propagate. Updated the pre-push hook itself to detect non-interactive/CI environments (no readable `/dev/tty`) and allow the push through silently rather than hang or fail. Added three new entries to `DEAD_ENDS.md` covering the alternatives considered (versioning `~/.claude/` directly, building this as a plugin, skipping the kit entirely in favor of built-ins).
**Why:** A /skeptic review surfaced five concrete issues: (1) `/review` and other command names collided with built-in skills, making it unclear which would win; (2) `install.sh` and `README.md` referenced a `skills/` folder that no longer existed — doc rot at v1.0; (3) the pre-push hook required manual per-project `cp` install, which would be skipped in practice and gave no upgrade path; (4) the hook would hard-fail in any CI/non-tty push because of unconditional `exec < /dev/tty`; (5) DEAD_ENDS.md was missing the strategic alternatives that justified the kit's existence at all.
**Result:** Commands now invoke unambiguously as `/jg-<name>`. `./install.sh` no longer references missing folders. `./install.sh --project <path>` is the one-liner for hook installation, and the symlink delivery means future hook edits propagate to every installed project automatically. CI pushes no longer hang on the tty prompt. The kit's rationale and the paths-not-taken are documented for future-me.
**Performance:** N/A
**Breaking changes:** Old `/review`, `/feasibility`, `/changelog`, `/status`, `/skeptic` slash commands no longer resolve to this kit's prompts — invoke as `/jg-*` instead. Any project that copied the old `pre-push` hook by hand still works, but won't auto-update; re-run `install.sh --project <path>` to convert to the symlink.
**Dependencies:** None
---
### v1.0 — 2026-05-20 23:30 PDT
**What changed:** Initial release of the Claude_Upgrade config kit. Includes: global `CLAUDE.md` defining communication style, feasibility-first workflow, code style, secrets/dependency hygiene, git/docs/judgment rules, and macOS environment notes; five self-contained slash commands (`/feasibility`, `/skeptic`, `/review`, `/changelog`, `/status`) with prompts inlined directly into the command files; three per-stack `CLAUDE.md` templates (`general`, `python`, `nextjs`); a `pre-push` git hook that warns before pushing to `main`; and an `install.sh` that symlinks `CLAUDE.md`, the `commands/` directory, and (when present) the `skills/` directory into `~/.claude/`.
**Why:** Manual copy-based install was friction that guaranteed the kit would rot across machines. Separate command-and-skill file pairs added an indirection layer that broke silently when the two were not co-located. Both fixed at v1.0 so the kit ships in a runnable state.
**Result:** One `git clone` + `./install.sh` brings up a consistent Claude Code environment on any machine. Symlinks (not copies) mean edits in the repo are live everywhere immediately. Re-running `install.sh` is safe — it skips already-correct links, replaces stale ones, and refuses to overwrite real files.
**Performance:** N/A
**Breaking changes:** None (initial release)
**Dependencies:** None
---
