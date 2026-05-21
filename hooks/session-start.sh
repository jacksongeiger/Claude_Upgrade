#!/usr/bin/env bash
# SessionStart hook for the Claude_Upgrade kit.
#
# When Claude Code starts a session (or runs /clear or /compact) in a directory
# that contains CHANGELOG.md or DEAD_ENDS.md, inject the latest CHANGELOG entry
# and the list of ruled-out dead ends as additionalContext, so the session
# starts already knowing the project's current state.
#
# In any other directory (no CHANGELOG.md, no DEAD_ENDS.md), the hook is a no-op
# — it must not fire context into random shell sessions.
#
# Output contract: emit `{}` for a no-op, or a SessionStart additionalContext
# JSON object per Claude Code's hook spec.

set -uo pipefail

# Defensive: if jq is missing, do nothing rather than break the session.
if ! command -v jq >/dev/null 2>&1; then
    echo '{}'
    exit 0
fi

# Resolve the project directory. Claude Code sets CLAUDE_PROJECT_DIR for
# SessionStart hooks; fall back to PWD otherwise.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$PROJECT_DIR" 2>/dev/null || { echo '{}'; exit 0; }

# Bail if no project markers in cwd.
if [ ! -f "CHANGELOG.md" ] && [ ! -f "DEAD_ENDS.md" ]; then
    echo '{}'
    exit 0
fi

context=""

# Extract the most recent CHANGELOG entry: content between the first two `---`
# horizontal rules. Assumes the kit's CHANGELOG format (most recent at top,
# separated by `---` lines).
if [ -f "CHANGELOG.md" ]; then
    latest=$(awk '
        BEGIN { in_entry = 0; count = 0 }
        /^---[[:space:]]*$/ {
            count++
            if (count == 1) { in_entry = 1; next }
            if (count == 2) { exit }
        }
        in_entry { print }
    ' CHANGELOG.md)
    if [ -n "$latest" ]; then
        context+="## Latest CHANGELOG entry"$'\n\n'"$latest"$'\n\n'
    fi
fi

# Extract dead-end titles (just the `### <title>` headers) so the session
# starts knowing what NOT to re-investigate. Bodies are skipped to keep the
# injection compact.
if [ -f "DEAD_ENDS.md" ]; then
    dead_ends=$(grep -E '^### ' DEAD_ENDS.md | sed 's/^### /- /')
    if [ -n "$dead_ends" ]; then
        context+="## Dead ends already ruled out (do not revisit)"$'\n\n'"$dead_ends"$'\n'
    fi
fi

if [ -z "$context" ]; then
    echo '{}'
    exit 0
fi

# Emit JSON with jq for safe escaping.
jq -n --arg ctx "$context" '{
    hookSpecificOutput: {
        hookEventName: "SessionStart",
        additionalContext: $ctx
    }
}'
