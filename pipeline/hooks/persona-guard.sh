#!/usr/bin/env bash
# Persona guard — PreToolUse hook scoped to the persona agent. The persona
# must know the product only through the browser: it may read and write its
# own run directory, and run the persona driver. Everything else is denied.
# Fails closed.
set -uo pipefail
input=$(cat)
command -v jq >/dev/null 2>&1 || { echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"persona guard: jq missing"}}'; exit 0; }
deny() { jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'; exit 0; }
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty')
case "$tool" in
  Read|Write)
    fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
    case "$fp" in
      */.pipeline/ux/*|*/.pipeline/ship/*/smoke/*|*/persona_driver.cjs|*/rubric.md) exit 0 ;;
      *) deny "the persona only knows the product through the browser; $fp is off limits" ;;
    esac ;;
  Bash)
    cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
    if printf '%s' "$cmd" | grep -Eq '^[[:space:]]*(node[[:space:]]+[^[:space:]]*persona_driver\.cjs|mkdir[[:space:]]+-p[[:space:]]+[^[:space:]]*\.pipeline/(ux|ship)|ls[[:space:]]+[^[:space:]]*\.pipeline/(ux|ship)|cat[[:space:]]+[^[:space:]]*\.pipeline/(ux|ship))'; then
        # no chaining onto something else
        if printf '%s' "$cmd" | grep -Eq '&&|\|\||;|\|[^|]|`|\$\('; then deny "one plain command at a time: $cmd"; fi
        exit 0
    fi
    deny "the persona may only drive the browser (persona_driver.cjs) and touch its run directory: $cmd" ;;
  *) exit 0 ;;
esac
