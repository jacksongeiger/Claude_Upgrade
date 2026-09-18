#!/usr/bin/env bash
# Nightshift event recorder — SubagentStart / SubagentStop, registered in the
# project's .claude/settings.json by init, async.
#
# No-op unless a loop is running (.loop/run/loop.pid exists), so a user's
# ordinary interactive sessions in the project never write anything.
# Appends one JSON line to events.jsonl and one human line to events.log —
# the statusline's third line and /jg-loop watch are built from these.

set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)

root="${CLAUDE_PROJECT_DIR:-$PWD}"
# A child claude -p runs inside .loop/wt/loop; the project root is the common
# git dir's parent.
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
if [ -n "$cwd" ]; then
    common=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) && root=$(dirname "$common")
fi
[ -f "$root/.loop/run/loop.pid" ] || exit 0

ev=$(printf '%s' "$input" | jq -r '.hook_event_name // empty')
id=$(printf '%s' "$input" | jq -r '.agent_id // empty')
type=$(printf '%s' "$input" | jq -r '.agent_type // empty')
ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

case "$ev" in
  SubagentStart) name="agent_start" ;;
  SubagentStop)  name="agent_stop" ;;
  *) exit 0 ;;
esac

jq -cn --arg ts "$ts" --arg e "$name" --arg id "$id" --arg t "$type" \
    '{ts:$ts,event:$e,agent_id:$id,agent_type:$t}' >> "$root/.loop/events.jsonl" 2>/dev/null
printf '%s %s %s %s\n' "$ts" "$(printf '%s' "$name" | tr a-z A-Z)" "$type" "$id" >> "$root/.loop/events.log" 2>/dev/null
exit 0
