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
# --settings hooks too, so this is the mechanism.
#
# Auth decides which directory comes back. On Linux ~/.claude/.credentials.json
# is linked into the kit-owned dir. On macOS OAuth lives in the Keychain, keyed
# to the config dir, so a kit-owned dir is simply logged out ("Not logged in ·
# Please run /login"); there an EMPTY string comes back and the driver must
# leave CLAUDE_CONFIG_DIR unset: setting it to any value, the default path
# included, re-keys the Keychain lookup (measured: `auth status` flips to
# loggedIn:false). The drivers' `--setting-sources project` keeps user hooks
# and plugins out on that path (measured: 0 plugins, only the --settings hooks
# fire). User agents, skills and ~/.claude/CLAUDE.md do still load there.
set -u
NSDIR="$1"; SETTINGS="$2"
USER_CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
CFG="$NSDIR/claude"
mkdir -p "$CFG"
cp "$SETTINGS" "$CFG/settings.json"
if [ -e "$USER_CFG/.credentials.json" ]; then
    [ -e "$CFG/.credentials.json" ] || ln -s "$USER_CFG/.credentials.json" "$CFG/.credentials.json" 2>/dev/null || true
    printf '%s' "$CFG"
else
    printf ''
fi
