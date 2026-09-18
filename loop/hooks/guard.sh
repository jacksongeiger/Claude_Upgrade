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

# Pinned scorer files: the judges of the code (an in-repo eval, its corpus, a
# bench). Listed as globs under scorers[].pins in the Nightshift config the
# driver exports as NIGHTSHIFT_CONFIG; the manifest hash check at score time
# is the lock, this is the early warning.
_pin_globs() {
    [ -n "${NIGHTSHIFT_CONFIG:-}" ] && [ -r "${NIGHTSHIFT_CONFIG}" ] || return 0
    jq -r '[.scorers[]? | select(.enabled != false) | .pins[]?] | .[]' "$NIGHTSHIFT_CONFIG" 2>/dev/null
}
_pinned() {  # _pinned <repo-relative path> → 0 if it matches a pin glob
    local rel="$1" g
    while IFS= read -r g; do
        [ -n "$g" ] || continue
        g=${g#./}
        # shellcheck disable=SC2254 - the glob is the point
        case "$rel" in $g) return 0 ;; esac
        # a pinned directory prefix (corpora/*.yaml covers corpora/x.yaml; a
        # write to the directory's parent path is not a file write)
    done <<EOF3
$(_pin_globs)
EOF3
    return 1
}

command -v jq >/dev/null 2>&1 || { echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"guard: jq missing"}}'; exit 0; }

tool=$(printf '%s' "$input" | jq -r '.tool_name // empty')
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty')
[ -n "$tool" ] || deny "guard could not read tool_name" "parse"
[ -n "$cwd" ] || deny "guard could not read cwd" "parse"

case "$tool" in
  Bash)
    cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
    # Safety trips: touching main, pushing, destructive history, redirecting
    # git at another checkout. These stop the whole run.
    if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+push|git[[:space:]]+(checkout|switch)[[:space:]]+(-[^[:space:]]+[[:space:]]+)*(main|master)\b|git[[:space:]]+branch[[:space:]]+-[fDd]|git[[:space:]]+merge\b|git[[:space:]]+rebase\b|git[[:space:]]+reset[[:space:]]+--hard|git[[:space:]]+worktree[[:space:]]+(add|remove|prune|move|lock|unlock)|gh[[:space:]]+pr[[:space:]]+merge|GIT_DIR=|GIT_WORK_TREE='; then
        deny "executors may only commit on their own branch: $cmd" "safety"
    fi
    # Scope denies: looking around other checkouts (git -C, worktree list).
    # Denied — the executor has everything it needs in its own worktree — but
    # read-only, so logged as "scope" and not a trip.
    if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+worktree\b'; then
        deny "stay in your own worktree; other checkouts are not yours to read: $cmd" "scope"
    fi
    # `git -C <own worktree>` is just a verbose way of saying `git`; any other
    # -C target is another checkout.
    if printf '%s' "$cmd" | grep -Eq 'git[[:space:]]+-C[[:space:]]'; then
        own=$(realpath -m "$cwd" 2>/dev/null || printf '%s' "$cwd")
        while read -r target; do
            [ -n "$target" ] || continue
            t=$(realpath -m "$target" 2>/dev/null || printf '%s' "$target")
            case "$t" in
              "$own"|"$own"/*) ;;
              *) deny "stay in your own worktree; other checkouts are not yours to read: $cmd" "scope" ;;
            esac
        done <<EOF2
$(printf '%s' "$cmd" | grep -oE 'git[[:space:]]+-C[[:space:]]+[^[:space:]]+' | awk '{print $3}' | tr -d '"'"'"'')
EOF2
    fi
    # Installing the project's own pinned manifest is setup, not acquisition:
    # a fresh worktree has no venv/node_modules and setup_cmd must be able to
    # build it. Anything naming a package is still denied below.
    if printf '%s' "$cmd" | grep -Eq '\bpip3?[[:space:]]+install[[:space:]]+(-q[[:space:]]+)?-r[[:space:]]+[^[:space:];|&]+(\.txt|\.lock)?([[:space:];|&]|$)|\bpip3?[[:space:]]+install[[:space:]]+(-q[[:space:]]+)?(-e[[:space:]]+)?['"'"'"]?\.(\[[A-Za-z0-9_,-]*\])?['"'"'"]?([[:space:];|&]|$)|\bnpm[[:space:]]+ci([[:space:]]|$)|\buv[[:space:]]+sync([[:space:]]|$)|\bpoetry[[:space:]]+install([[:space:]]|$)|\bgo[[:space:]]+mod[[:space:]]+download|\bcargo[[:space:]]+fetch'; then
        # A package name after the manifest is acquisition; a redirect, pipe,
        # separator or flag is not (`-r requirements.txt 2>&1 | tail` was
        # being denied as if "2>&1" were a package).
        if ! printf '%s' "$cmd" | grep -Eq '\bpip3?[[:space:]]+install[[:space:]]+(-q[[:space:]]+)?(-r[[:space:]]+[^[:space:];|&]+|(-e[[:space:]]+)?['"'"'"]?\.(\[[A-Za-z0-9_,-]*\])?['"'"'"]?)[[:space:]]+[A-Za-z_][^[:space:]]*'; then
            exit 0
        fi
    fi
    # Editing a pinned scorer file through the shell (redirect, sed -i, tee,
    # mv, cp, rm, truncate) is the same trip as editing it with Write.
    if printf '%s' "$cmd" | grep -Eq '(>|>>|\bsed[[:space:]]+-i|\btee\b|\bmv\b|\bcp\b|\brm\b|\btruncate\b|\bpatch\b)'; then
        for tok in $(printf '%s' "$cmd" | tr '|;&()<>' '       ' | tr -s ' '); do
            t=${tok#./}
            t=${t#"$cwd"/}
            _pinned "$t" && deny "pinned scorer file, not the executor's to change: $t" "safety"
        done
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
    # Scratch files in a temp root (the session scratchpad lives under
    # /tmp/claude-*) are harmless; any other path outside the worktree —
    # another checkout above all — is a safety trip.
    tmproot=$(realpath -m "${TMPDIR:-/tmp}" 2>/dev/null || printf '%s' "${TMPDIR:-/tmp}")
    case "$real" in
      "$wt"/*) ;;
      /tmp/*|/private/tmp/*|/var/folders/*|"$tmproot"/*)
        # a temp path that is inside some git checkout is another project, not scratch
        if git -C "$(dirname "$real")" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
            deny "write into another checkout: $real" "safety"
        fi
        exit 0 ;;
      *) deny "write outside the executor worktree: $real" "safety" ;;
    esac
    rel=${real#"$wt"/}
    case "$rel" in
      .claude/*|.loop/*|.git/*|.claude|.loop|.git)
        deny "writes under .claude/ .loop/ .git/ are denied: $rel" "safety" ;;
    esac
    _pinned "$rel" && deny "pinned scorer file, not the executor's to change: $rel" "safety"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
