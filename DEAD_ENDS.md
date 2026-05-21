# Dead Ends

A log of approaches investigated and ruled out, so the same ground isn't covered twice.

---
### Separate command and skill files
**Idea:** Keep slash-command files (`commands/<name>.md`) as thin wrappers that point to a skill file (`skills/<name>.md`), so a single skill could be reused across multiple commands.
**Investigated:** 2026-05-20
**Ruled out because:** No commands shared skills — every command had a 1:1 match with a skill, so the indirection delivered zero reuse benefit. Worse, it created a silent failure mode: when the command file was copied to `~/.claude/commands/` but the skill file was not co-located at the expected relative path, the reference broke without warning and the model would hallucinate the missing content. Inlining each prompt directly into its command file is simpler and fails loudly if anything is missing.
---
