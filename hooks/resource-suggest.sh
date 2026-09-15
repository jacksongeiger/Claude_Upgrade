#!/usr/bin/env bash
# UserPromptSubmit hook for the rdx resource-discovery system.
#
# This runs on EVERY prompt, so it does only free checks in bash and execs
# Python solely when a suggestion is actually plausible. A python3 cold start
# is ~40-60ms on macOS; paying that unconditionally forever is the wrong trade
# when >95% of prompts will be silent.
#
# Output contract: `{}` for a no-op, or a UserPromptSubmit additionalContext
# object per Claude Code's hook spec.
#
# THIS SCRIPT ALWAYS EXITS 0. Exit code 2 would block the user's prompt
# entirely; no failure in a suggestion system justifies that.

set -uo pipefail

emit_noop() { echo '{}'; exit 0; }

STATE_DIR="${RDX_STATE_DIR:-$HOME/.claude/rdx}"
DB="$STATE_DIR/index.db"
LOG="$STATE_DIR/rdx.log"
REPO_DIR="__REPO_DIR__"
DISCOVERY_DIR="$REPO_DIR/discovery"
PY="$DISCOVERY_DIR/venv/bin/python"
# The hook runs with the user's cwd, not ours, so the package root has to be
# put on the path explicitly or `-m rdx.hook` cannot find it.
export PYTHONPATH="$DISCOVERY_DIR${PYTHONPATH:+:$PYTHONPATH}"

# --- free bail-outs, cheapest first --------------------------------------

command -v jq >/dev/null 2>&1 || emit_noop

# Kill switches: a file, an env var, or a per-project opt-out.
[ -f "$STATE_DIR/DISABLED" ] && emit_noop
case "${RDX_DISABLE:-0}" in 1|true|yes|on) emit_noop ;; esac
[ -f ".rdx-off" ] && emit_noop

# No index means nothing to suggest.
[ -s "$DB" ] || emit_noop
[ -x "$PY" ] || emit_noop

# --- read the payload once ------------------------------------------------

payload="$(cat)"
[ -n "$payload" ] || emit_noop

prompt="$(printf '%s' "$payload" | jq -r '.prompt // ""' 2>/dev/null)" || emit_noop
[ -n "$prompt" ] || emit_noop

# --- trivial-prompt filter, pure bash ------------------------------------
# Mirrors the Python gate's first condition so the common case never pays for
# an interpreter start.

# Slash commands are never tool-acquisition prompts.
case "$prompt" in /*) emit_noop ;; esac

# Too short to carry intent.
if [ "${#prompt}" -lt 25 ]; then emit_noop; fi

# Fewer than 4 whitespace-separated tokens.
set -- $prompt
if [ "$#" -lt 4 ]; then emit_noop; fi

# --- hand off to Python ---------------------------------------------------

mkdir -p "$STATE_DIR" 2>/dev/null

out="$(printf '%s' "$payload" | "$PY" -m rdx.hook 2>>"$LOG")"
status=$?

if [ "$status" -ne 0 ] || [ -z "$out" ]; then
    emit_noop
fi

# Never emit anything that is not valid JSON.
if ! printf '%s' "$out" | jq -e . >/dev/null 2>&1; then
    emit_noop
fi

printf '%s' "$out"
exit 0
