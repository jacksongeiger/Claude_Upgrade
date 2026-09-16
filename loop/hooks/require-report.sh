#!/usr/bin/env bash
# Nightshift executor report gate — Stop hook scoped to exec-sonnet / exec-opus.
#
# An executor's final message must be a single JSON object with a "status"
# field. Without it the planner cannot triage the result, so the executor is
# sent back to write it. Three refusals and we let it stop: the driver then
# treats the subtask as failed, which is a visible outcome. An infinite block
# is not.
#
# Input: Stop JSON on stdin (last_assistant_message, stop_hook_active, cwd).
# Output: a deny decision with the reason, or nothing.

set -uo pipefail
input=$(cat)
command -v jq >/dev/null 2>&1 || exit 0

msg=$(printf '%s' "$input" | jq -r '.last_assistant_message // empty')
cwd=$(printf '%s' "$input" | jq -r '.cwd // "."')

# Extract the last {...} block and check it parses with a status field.
ok=0
if [ -n "$msg" ]; then
    block=$(printf '%s' "$msg" | python3 -c '
import sys, json, re
s = sys.stdin.read()
# take the last top-level JSON object in the message
end = s.rfind("}")
depth = 0
start = -1
for i in range(end, -1, -1):
    c = s[i]
    if c == "}": depth += 1
    elif c == "{":
        depth -= 1
        if depth == 0:
            start = i; break
if start < 0: sys.exit(1)
try:
    obj = json.loads(s[start:end+1])
except Exception:
    sys.exit(1)
if not isinstance(obj, dict) or obj.get("status") not in ("done","ambiguous","blocked","failed"):
    sys.exit(1)
print("ok")
' 2>/dev/null) && ok=1
fi

[ "$ok" = "1" ] && exit 0

# Count refusals in a file beside the worktree so it survives across turns.
counter="$cwd/.nightshift-stop-blocks"
n=0; [ -f "$counter" ] && n=$(cat "$counter" 2>/dev/null || echo 0)
n=$((n + 1)); printf '%s' "$n" > "$counter" 2>/dev/null || true
if [ "$n" -gt 3 ]; then
    exit 0
fi

jq -n '{hookSpecificOutput:{hookEventName:"Stop", permissionDecision:"deny",
  permissionDecisionReason:"Your final message must be exactly one JSON object with a status field (done|ambiguous|blocked|failed), branch, worktree, commit, files, test_output_tail, question, decisions_made. Write it now."}}'
exit 0
