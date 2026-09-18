#!/usr/bin/env bash
# child_config.sh — a kit-owned CLAUDE_CONFIG_DIR for one project's children.
#
#   CLAUDE_CONFIG_DIR=$(bash child_config.sh <nightshift slug dir> <child-settings.json>)
#
# Every `claude -p` child (planner, executor, reviewer, validation roles) reads
# its settings from this directory and nothing else: the user's
# ~/.claude/settings.json hooks, installed plugins and their hooks (ECC's
# GateGuard, dev-server block, format/typecheck and session-evaluator Stop
# hooks would otherwise run inside every executor), user-level agents and
# commands never load. `--safe-mode` was measured to drop the kit's own
# --settings hooks too, so this is the mechanism. Auth: macOS keeps OAuth in
# the Keychain; on Linux ~/.claude/.credentials.json is linked in.
set -u
NSDIR="$1"; SETTINGS="$2"
CFG="$NSDIR/claude"
mkdir -p "$CFG"
cp "$SETTINGS" "$CFG/settings.json"
for f in .credentials.json; do
    [ -e "$HOME/.claude/$f" ] && [ ! -e "$CFG/$f" ] && ln -s "$HOME/.claude/$f" "$CFG/$f" 2>/dev/null || true
done
printf '%s' "$CFG"
