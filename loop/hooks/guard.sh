#!/usr/bin/env bash
# Nightshift executor guard — PreToolUse hook scoped to exec-sonnet / exec-opus.
#
# Denies the things an executor must never do, and logs every deny to the
# project's events stream so the driver can trip on it. This is a lock, not an
# instruction: the executor's prompt says the same things, but the prompt is
# advisory and this is not.
#
# Input: the PreToolUse JSON on stdin. `cwd` is the executor's worktree root
# (Claude Code documents that cwd follows the worktree in hook input).
# Output: a permissionDecision JSON on deny; nothing (exit 0) on allow.
# Never exits non-zero on its own errors — a broken guard must fail CLOSED, so
# on any parse failure it denies.

set -uo pipefail

input=$(cat)

deny() {
    local reason="$1" kind="$2"
    _log_deny "$reason" "$kind"
    jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",
        permissionDecision:"deny", permissionDecisionReason:$r}}'
    exit 0
}

_log_deny() {
    local reason="$1" kind="$2"
    local root; root=$(_project_root)
    [ -n "$root" ] || return 0
    mkdir -p "$root/.loop" 2>/dev/null || return 0
    local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    jq -cn --arg ts "$ts" --arg r "$reason" --arg k "$kind" --arg t "$tool" \
        '{ts:$ts,event:"deny",kind:$k,tool:$t,reason:$r}' >> "$root/.loop/events.jsonl" 2>/dev/null
    printf '%s DENY %s %s\n' "$ts" "$kind" "$reason" >> "$root/.loop/events.log" 2>/dev/null
}

# The project root is the main checkout that owns this worktree: the common
# git dir's parent. Falls back to CLAUDE_PROJECT_DIR.
_project_root() {
    local common
    common=$(git -C "$cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || true
    if [ -n "$common" ]; then dirname "$common"; return; fi
    printf '%s' "${CLAUDE_PROJECT_DIR:-}"
}

command -v jq >/dev/null 2>&1 || { echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"guard: jq missing"}}'; exit 0; }

tool=$(printf '%s' "$input" | jq -r '.tool_name // empty')
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$tool" ] || deny "guard could not read tool_name" "parse"
[ -n "$cwd" ] || deny "guard could not read cwd" "parse"

case "$tool" in
  Bash)
    cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
    # Safety trips: touching main, pushing, leaving the worktree.
    if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push|git[[:space:]]+(checkout|switch)[[:space:]]+(-[^[:space:]]+[[:space:]]+)*(main|master)\b|git[[:space:]]+branch[[:space:]]+-[fDd]|git[[:space:]]+merge\b|git[[:space:]]+rebase\b|git[[:space:]]+reset[[:space:]]+--hard|git[[:space:]]+worktree\b|gh[[:space:]]+pr[[:space:]]+merge|git[[:space:]]+-C[[:space:]]|GIT_DIR=|GIT_WORK_TREE='; then
        deny "executors may only commit on their own branch: $cmd" "safety"
    fi
    # Installing the project's own pinned manifest is setup, not acquisition:
    # a fresh worktree has no venv/node_modules and setup_cmd must be able to
    # build it. Anything naming a package is still denied below.
    if printf '%s' "$cmd" | grep -Eq '\bpip3?[[:space:]]+install[[:space:]]+(-q[[:space:]]+)?-r[[:space:]]+[^[:space:]]+(\.txt|\.lock)?([[:space:]]|$)|\bnpm[[:space:]]+ci([[:space:]]|$)|\buv[[:space:]]+sync([[:space:]]|$)|\bgo[[:space:]]+mod[[:space:]]+download|\bcargo[[:space:]]+fetch'; then
        if ! printf '%s' "$cmd" | grep -Eq '\bpip3?[[:space:]]+install[[:space:]]+(-q[[:space:]]+)?-r[[:space:]]+[^[:space:]]+[[:space:]]+[^-[:space:]]'; then
            exit 0
        fi
    fi
    # Installs and escapes.
    if printf '%s' "$cmd" | grep -Eq '\brdx[[:space:]]+install\b|\bnpm[[:space:]]+(i|install|ci)\b|\bpip3?[[:space:]]+install\b|\buv[[:space:]]+(add|pip)\b|\bbrew[[:space:]]|\bcargo[[:space:]]+(add|install)\b|curl[^|]*\|[[:space:]]*(ba)?sh\b|\bsudo[[:space:]]|rm[[:space:]]+-rf[[:space:]]+(/|~|\$HOME|\.\.)|\bclaude[[:space:]]+plugin\b|\bclaude[[:space:]]+mcp\b'; then
        deny "unattended run: installs and escapes are denied, record the need in your report: $cmd" "install"
    fi
    exit 0
    ;;
  Write|Edit|MultiEdit)
    fp=$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty')
    [ -n "$fp" ] || deny "guard could not read file_path" "parse"
    case "$fp" in /*) ;; *) fp="$cwd/$fp" ;; esac
    # realpath -m resolves without requiring existence (GNU); fall back to python.
    real=$(realpath -m "$fp" 2>/dev/null || python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$fp")
    wt=$(realpath -m "$cwd" 2>/dev/null || python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$cwd")
    case "$real" in
      "$wt"/*) ;;
      *) deny "write outside the executor worktree: $real" "safety" ;;
    esac
    rel=${real#"$wt"/}
    case "$rel" in
      .claude/*|.loop/*|.git/*|.claude|.loop|.git)
        deny "writes under .claude/ .loop/ .git/ are denied: $rel" "safety" ;;
    esac
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
