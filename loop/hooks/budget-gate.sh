#!/usr/bin/env bash
# Nightshift budget gate — PreToolUse hook on Agent, registered in the project's
# .claude/settings.json by init.
#
# Denies a new subagent spawn when the run is at its cap. The primary cost
# control is the driver (tail.py + process-group kill); this hook is the belt
# that stops a fan-out from STARTING once the money is gone, which the driver
# cannot see until the child's next stream line.
#
# No-op unless a loop is running.

set -uo pipefail
command -v jq >/dev/null 2>&1 || exit 0
input=$(cat)

cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
root="${CLAUDE_PROJECT_DIR:-$PWD}"
if [ -n "$cwd" ]; then
    common=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) && root=$(dirname "$common")
fi
state="$root/.loop/state.json"
[ -f "$root/.loop/run/loop.pid" ] || exit 0
[ -f "$state" ] || exit 0

over=$(jq -r '
  ((.spent_usd // 0) + (.live_spend_usd // 0) + (.min_iter_usd // 1.5)) >= (.cap_usd // 1e9)
' "$state" 2>/dev/null)

if [ "$over" = "true" ]; then
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    jq -cn --arg ts "$ts" '{ts:$ts,event:"deny",kind:"budget",tool:"Agent",reason:"cap reached"}' >> "$root/.loop/events.jsonl" 2>/dev/null
    printf '%s DENY budget Agent spawn refused: cap reached\n' "$ts" >> "$root/.loop/events.log" 2>/dev/null
    jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",
        permissionDecisionReason:"Nightshift: cost cap reached, no new agents. Write your summary and stop."}}'
fi
exit 0
