#!/usr/bin/env bash
# PostToolUse hook: append one JSONL line per tool call to the rdx spool.
#
# This fires hundreds of times per session, so it does the absolute minimum:
# no sqlite3 process (which would add latency to every tool call and contend
# with the ingest write lock), no python, just jq and an append. `rdx stats`
# drains the spool.
#
# ALWAYS EXITS 0. A measurement hook must never interfere with a tool call.

set -uo pipefail

STATE_DIR="${RDX_STATE_DIR:-$HOME/.claude/rdx}"
SPOOL="$STATE_DIR/toolevents.jsonl"

command -v jq >/dev/null 2>&1 || exit 0
[ -f "$STATE_DIR/DISABLED" ] && exit 0
case "${RDX_DISABLE:-0}" in 1|true|yes|on) exit 0 ;; esac
[ -d "$STATE_DIR" ] || exit 0

payload="$(cat)"
[ -n "$payload" ] || exit 0

# Only the fields measurement needs. Tool inputs are never recorded: they
# contain file contents, prompts and arguments that have no business sitting
# in a log on disk.
printf '%s' "$payload" | jq -c --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{ts: $ts, session_id: .session_id, tool_name: .tool_name}' \
  >> "$SPOOL" 2>/dev/null

# Keep the spool bounded: if it grows past ~5MB something is not draining it.
if [ -f "$SPOOL" ]; then
    size=$(wc -c < "$SPOOL" 2>/dev/null || echo 0)
    if [ "$size" -gt 5242880 ]; then
        tail -c 1048576 "$SPOOL" > "$SPOOL.tmp" 2>/dev/null && mv "$SPOOL.tmp" "$SPOOL"
    fi
fi

exit 0
