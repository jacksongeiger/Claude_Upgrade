# Dead Ends

A log of approaches investigated and ruled out, so the same ground isn't covered twice.

---
### Maintaining a custom `/jg-review` slash command
**Idea:** Ship a personal code-review command (`commands/jg-review.md`) running a five-pass checklist (Architecture → Risk → Security → Performance → Maintainability) with "fix easy issues inline" behavior.
**Investigated:** 2026-05-21 — direct read of source files for the official `/review` plugin (`~/.claude/plugins/marketplaces/claude-plugins-official/plugins/code-review/commands/code-review.md`) and `superpowers:requesting-code-review` (`~/.claude/plugins/marketplaces/superpowers-dev/skills/requesting-code-review/SKILL.md` + `code-reviewer.md`).
**Ruled out because:** `jg-review` was a strict subset of what the official `/review` pipeline already does, and materially weaker on every dimension that matters. Specifically `jg-review` lacked: (1) confidence scoring of issues (the official `/review` runs a Haiku scorer per issue and filters anything below 80, eliminating nitpicks); (2) git history context (the official pipeline reads git blame and comments on prior PRs touching the same files); (3) file:line severity-tagged output (the superpowers reviewer outputs Critical/Important/Minor with file:line refs, while `jg-review` was free-form); (4) parallelism (the official pipeline runs five Sonnet review agents in parallel plus per-issue scorers — `jg-review` was single-agent). The two things `jg-review` did uniquely — no-PR-required execution and "fix easy issues inline" — are trivially achievable by invoking `/code-review` or `requesting-code-review` with one extra sentence. The CLAUDE.md personalization argument did not hold either: the official `/review`'s Agent 1 reads CLAUDE.md *dynamically*, catching more than `jg-review`'s three hardcoded checks (`.env` in `.gitignore`, pinned deps, hardcoded secrets) and adapting when CLAUDE.md changes. Keeping `jg-review` cost worse reviews under muscle-memory invocation; deleting it cost a one-time retraining to reach for `/code-review` / `/review <PR>` / `requesting-code-review` instead.
---
### Versioning `~/.claude/` directly with git
**Idea:** `git init ~/.claude` and version the live Claude Code config directory directly, instead of maintaining a separate repo and symlinking into it.
**Investigated:** 2026-05-20
**Ruled out because:** `~/.claude/` contains machine-specific cache, session state, telemetry, plugin data, and installed third-party skills that should not be versioned or synced across machines. A separate repo with explicit symlinks keeps the versioned content clean and intentional.
---
### Building this as a Claude Code plugin
**Idea:** Package the commands and CLAUDE.md preamble as a proper Claude Code plugin and distribute via the plugin marketplace.
**Investigated:** 2026-05-20
**Ruled out because:** For purely personal use, plugin packaging adds setup overhead (manifest, versioning, marketplace metadata) and loses the ability to edit a command in seconds with a normal text editor. Symlinks give instant edit-everywhere with zero ceremony. Worth revisiting if this is ever shared with others.
---
### Whether the kit was needed at all given existing superpowers/built-in skills
**Idea:** Skip building a custom kit entirely and rely on built-in Claude Code commands (`/review`, `/security-review`, etc.) and the superpowers plugin (`brainstorming`, `code-review`, `verify`, etc.), since they overlap with most of what `/jg-*` commands do.
**Investigated:** 2026-05-20
**Ruled out because:** (1) The feasibility-first rule and incremental-testing rule are personal workflow rules not covered by any built-in. (2) The CLAUDE.md preamble encodes personal environment details (macOS Apple Silicon, zsh, Homebrew Node, Swift available) and personal preferences (always pin requirements.txt, never push without confirmation) that built-ins can't infer. (3) Named slash commands build personal muscle memory; the cost of `/jg-skeptic` shadowing nothing while the built-in `skeptic` skill stays available is essentially zero. The overlap is acceptable; the personal layer is the value.
---
### Separate command and skill files
**Idea:** Keep slash-command files (`commands/<name>.md`) as thin wrappers that point to a skill file (`skills/<name>.md`), so a single skill could be reused across multiple commands.
**Investigated:** 2026-05-20
**Ruled out because:** No commands shared skills — every command had a 1:1 match with a skill, so the indirection delivered zero reuse benefit. Worse, it created a silent failure mode: when the command file was copied to `~/.claude/commands/` but the skill file was not co-located at the expected relative path, the reference broke without warning and the model would hallucinate the missing content. Inlining each prompt directly into its command file is simpler and fails loudly if anything is missing.
---
