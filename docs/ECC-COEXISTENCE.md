# ECC and the kit: coexistence

Checked 2026-09-18 against ECC 2.2.1 (`affaan-m/everything-claude-code`, 68
agents, 292 skills, 94 commands, 24 hook entries), statically, from a clone.
No Mac run was needed for the part that matters.

## Names

No collisions. ECC's commands have no `jg-` prefix and no `ard`; its agents
(`planner`, `code-reviewer`, `security-reviewer`, …) do not share a name
with the kit's (`reviewer`, `exec-sonnet`, `exec-opus`, `persona`,
`persona-judge`, `evidence-judge`, `chore`, `test-assessor`, `ui-auditor`).
ECC ships a statusline as an example to copy by hand, not an installed one;
the kit's statusline stays.

## Hooks: the real risk

ECC registers hooks on PreToolUse (Bash, Write, Edit and `.*`), PostToolUse
(`.*`, 30–45 s timeouts), seven Stop hooks (one a session evaluator with a
300 s timeout, one a batch format-and-typecheck), SessionStart, SessionEnd
and PreCompact. Hooks in the user's settings and in enabled plugins run in
every `claude -p` child too. Inside a Nightshift or build child that means:

- GateGuard's "fact-forcing gate" blocks an edit or a command until the model
  has "investigated": every executor on the ponytail ladder would be stopped
  at its first edit.
- `pre-bash-dev-server-block` blocks dev-server commands: the persona and
  lighthouse scorers, which serve the app from inside the child, would fail.
- The Stop hooks run a formatter and typechecker on the files an executor
  edited and spawn a session evaluation: file changes after the executor's
  commit (scope drift for `check_plan --verify`), plus time and spend on
  every child stop.

Measured here: `--safe-mode` disables the kit's own `--settings` hooks along
with everything else (a PreToolUse marker hook fired without the flag, not
with it), so it cannot be the answer. `CLAUDE_CONFIG_DIR` can: a child
pointed at a directory that holds only the kit's `settings.json`
authenticated, ran, and fired the kit's hook, with nothing from `~/.claude`.

## What the kit does now

`loop/child_config.sh` writes `~/.claude/nightshift/<slug>/claude/settings.json`
(the child's allowlist and hooks) and every spawn site — `loop/run.sh`,
`pipeline/child.sh`, `pipeline/build.sh` — exports
`CLAUDE_CONFIG_DIR` to it. Children therefore never load user hooks,
plugins, plugin hooks, user-level agents or commands, or the global
`~/.claude/CLAUDE.md` (the project's `CLAUDE.md` still applies in the
worktree). On Linux the OAuth file is linked in; on macOS auth lives in the
Keychain. `loop/tests/test_run.sh` asserts the child saw that directory.

## Interactive sessions

ECC and the kit coexist in the human's own session: both sets of hooks run
there, which is the human's choice. The kit's commands and agents are
namespaced; ECC's GateGuard will ask the human's session to investigate
before edits, which is its purpose. Nothing in the kit needs ECC, and no ship
row depends on it (the AgentShield row stays out until a human decides to
install ECC and wants it).
