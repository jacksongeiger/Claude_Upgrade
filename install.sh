#!/usr/bin/env bash
# install.sh — set up Claude Code config kit symlinks into ~/.claude/.
#
# Usage:
#   ./install.sh                           Install global kit (CLAUDE.md + commands + SessionStart hook).
#   ./install.sh --project /path/to/repo   Install the pre-push hook into that repo's .git/hooks/.
#   ./install.sh --plugins                 Install the recommended plugins (--plugins --dry-run just prints them).
#   ./install.sh --discovery               Install the rdx resource-discovery system (venv, index, hooks, statusline).
#   ./install.sh --discovery-uninstall     Remove the rdx hooks and statusline (leaves the index on disk).
#   ./install.sh --discovery-off           Disable rdx without uninstalling it.
#
# Re-runnable: skips links that already point to the right place, replaces
# stale symlinks, and refuses to overwrite real files. The SessionStart hook
# registration in ~/.claude/settings.json is dedupe-safe by command path.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
COMMANDS_DIR="$CLAUDE_DIR/commands"
SETTINGS_FILE="$CLAUDE_DIR/settings.json"

install_hook_into_project() {
    local project="$1"

    if [ -z "$project" ]; then
        echo "✗ --project requires a path argument"
        exit 1
    fi
    if [ ! -d "$project" ]; then
        echo "✗ not a directory: $project"
        exit 1
    fi
    if [ ! -d "$project/.git" ]; then
        echo "✗ not a git repository: $project (no .git/ directory)"
        exit 1
    fi

    local hooks_dir="$project/.git/hooks"
    local dst="$hooks_dir/pre-push"
    local src="$REPO_DIR/hooks/pre-push"

    mkdir -p "$hooks_dir"

    if [ -e "$dst" ] && [ ! -L "$dst" ]; then
        echo "✗ refusing to overwrite existing non-symlink hook: $dst (move it aside and re-run)"
        exit 1
    fi
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        echo "  already linked: $dst"
        exit 0
    fi
    [ -L "$dst" ] && rm "$dst"

    ln -s "$src" "$dst"
    chmod +x "$src"
    echo "✓ installed pre-push hook into $project"
}

RECOMMENDED_PLUGINS="feature-dev commit-commands playwright serena code-simplifier"

# Install the recommended plugins.
#
# CORRECTION (v1.10): the v1.9 note claiming plugin installs "cannot be
# auto-run from a shell" is no longer true. `claude plugin install` has been a
# non-interactive CLI since Claude Code 2.1.x, with --scope, -y and --json.
# We still fall back to printing when the CLI is missing or too old.
install_plugins() {
    local dry_run="${1:-}"

    if [ -n "$dry_run" ] || ! command -v claude >/dev/null 2>&1; then
        [ -n "$dry_run" ] || echo "claude CLI not found — printing commands instead:"
        echo ""
        for p in $RECOMMENDED_PLUGINS; do
            echo "claude plugin install $p@claude-plugins-official --scope user"
        done
        echo ""
        echo "See PLUGINS.md for the full inventory."
        return 0
    fi

    echo "Adding the official marketplace (idempotent)…"
    claude plugin marketplace add anthropics/claude-plugins-official >/dev/null 2>&1 || true

    local ok=0 bad=0
    for p in $RECOMMENDED_PLUGINS; do
        if claude plugin install "$p@claude-plugins-official" --scope user -y >/dev/null 2>&1; then
            echo "  ✓ $p"
            ok=$((ok + 1))
        else
            echo "  ✗ $p (install failed — try manually)"
            bad=$((bad + 1))
        fi
    done
    echo ""
    echo "$ok installed, $bad failed. See PLUGINS.md for the full inventory."
}

failures=0

link() {
    local src="$1"
    local dst="$2"

    if [ -L "$dst" ]; then
        if [ "$(readlink "$dst")" = "$src" ]; then
            echo "  already linked: $dst"
            return 0
        fi
        echo "  replacing stale symlink: $dst"
        rm "$dst"
    elif [ -e "$dst" ]; then
        echo "✗ refusing to overwrite non-symlink: $dst (move it aside and re-run)"
        failures=$((failures + 1))
        return 1
    fi

    if ln -s "$src" "$dst"; then
        echo "✓ linked $dst -> $src"
    else
        echo "✗ failed to link $dst"
        failures=$((failures + 1))
    fi
}

# Register one hook event from hooks/hooks.json into ~/.claude/settings.json.
#
# Reads hooks/hooks.json, substitutes __REPO_DIR__ with the actual repo path,
# then merges that event's entry into settings.json under .hooks.<EVENT>.
# Dedupe-safe: any existing entry pointing at the same command path is replaced.
#
# Generalized from the original SessionStart-only version so the discovery
# system can register UserPromptSubmit through the identical code path rather
# than a second, divergent copy of the jq merge.
register_hook() {
    local event="$1"
    local hook_script="$2"
    local hook_template="$REPO_DIR/hooks/hooks.json"

    if ! command -v jq >/dev/null 2>&1; then
        echo "⚠ jq not available — skipping $event hook registration"
        return 0
    fi
    if [ ! -f "$hook_template" ]; then
        echo "⚠ $hook_template missing — skipping $event hook registration"
        return 0
    fi

    chmod +x "$hook_script" 2>/dev/null || true

    [ -f "$SETTINGS_FILE" ] || echo '{}' > "$SETTINGS_FILE"

    local resolved
    resolved=$(jq --arg repo "$REPO_DIR" '
        walk(if type == "string" then gsub("__REPO_DIR__"; $repo) else . end)
    ' "$hook_template") || {
        echo "✗ failed to resolve $hook_template placeholders"
        failures=$((failures + 1))
        return 1
    }

    local new_entry
    new_entry=$(echo "$resolved" | jq --arg ev "$event" '.hooks[$ev][0]') || {
        echo "✗ failed to extract $event entry from $hook_template"
        failures=$((failures + 1))
        return 1
    }
    if [ "$new_entry" = "null" ] || [ -z "$new_entry" ]; then
        echo "✗ no $event entry in $hook_template"
        failures=$((failures + 1))
        return 1
    fi

    local tmp
    tmp=$(mktemp)
    if jq --arg cmd "$hook_script" --arg ev "$event" --argjson entry "$new_entry" '
        .hooks = (.hooks // {})
        | .hooks[$ev] = ((.hooks[$ev] // [])
            | map(select((.hooks // []) | all(.command != $cmd))))
        | .hooks[$ev] += [$entry]
    ' "$SETTINGS_FILE" > "$tmp"; then
        mv "$tmp" "$SETTINGS_FILE"
        echo "✓ registered $event hook in $SETTINGS_FILE"
    else
        rm -f "$tmp"
        echo "✗ failed to merge $event hook into $SETTINGS_FILE"
        failures=$((failures + 1))
    fi
}

# Remove a hook entry by command path. Backs --discovery-uninstall, and closes
# the "no --uninstall mode yet" gap noted in the v1.6 CHANGELOG entry.
unregister_hook() {
    local event="$1"
    local hook_script="$2"

    command -v jq >/dev/null 2>&1 || return 0
    [ -f "$SETTINGS_FILE" ] || return 0

    local tmp
    tmp=$(mktemp)
    if jq --arg cmd "$hook_script" --arg ev "$event" '
        if .hooks[$ev] then
          .hooks[$ev] = (.hooks[$ev]
            | map(select((.hooks // []) | all(.command != $cmd))))
        else . end
    ' "$SETTINGS_FILE" > "$tmp"; then
        mv "$tmp" "$SETTINGS_FILE"
        echo "✓ unregistered $event hook ($hook_script)"
    else
        rm -f "$tmp"
    fi
}

# --------------------------------------------------------------------------
# rdx — resource discovery
# --------------------------------------------------------------------------

DISCOVERY_DIR="$REPO_DIR/discovery"
SUGGEST_HOOK="$REPO_DIR/hooks/resource-suggest.sh"
OBSERVE_HOOK="$REPO_DIR/hooks/tool-observe.sh"
RDX_STATE="$HOME/.claude/rdx"

install_discovery() {
    echo "Installing rdx (resource discovery) from $DISCOVERY_DIR"
    echo ""

    command -v python3 >/dev/null 2>&1 || {
        echo "✗ python3 not found"; exit 1; }

    # FTS5 is non-negotiable and macOS system Python sometimes ships without
    # it. Fail here, loudly, rather than inside a hook that must stay silent.
    if ! python3 -c "import sqlite3; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE t USING fts5(a)')" 2>/dev/null; then
        echo "✗ this python3's sqlite3 has no FTS5 support."
        echo "  Fix: install Homebrew python3 (brew install python) and re-run,"
        echo "  or point PATH at a python3 whose sqlite3 was built with FTS5."
        exit 1
    fi
    echo "✓ FTS5 available"

    if [ ! -x "$DISCOVERY_DIR/venv/bin/python" ]; then
        python3 -m venv "$DISCOVERY_DIR/venv" || { echo "✗ venv failed"; exit 1; }
        echo "✓ created venv"
    fi
    "$DISCOVERY_DIR/venv/bin/pip" install --quiet --disable-pip-version-check \
        -r "$DISCOVERY_DIR/requirements.txt" || {
        echo "✗ dependency install failed"; exit 1; }
    echo "✓ dependencies installed"

    mkdir -p "$RDX_STATE"
    "$DISCOVERY_DIR/rdx.sh" init || { echo "✗ rdx init failed"; exit 1; }

    mkdir -p "$HOME/.local/bin"
    link "$DISCOVERY_DIR/rdx.sh" "$HOME/.local/bin/rdx"

    chmod +x "$SUGGEST_HOOK" 2>/dev/null || true
    register_hook UserPromptSubmit "$SUGGEST_HOOK"
    chmod +x "$OBSERVE_HOOK" 2>/dev/null || true
    register_hook PostToolUse "$OBSERVE_HOOK"

    # Statusline: the only always-visible surface Claude Code exposes.
    if command -v jq >/dev/null 2>&1; then
        local tmp
        tmp=$(mktemp)
        if jq --arg cmd "$DISCOVERY_DIR/rdx.sh statusline" '
            .statusLine = {type: "command", command: $cmd, refreshInterval: 10}
        ' "$SETTINGS_FILE" > "$tmp"; then
            mv "$tmp" "$SETTINGS_FILE"
            echo "✓ registered statusline in $SETTINGS_FILE"
        else
            rm -f "$tmp"
        fi
    fi

    echo ""
    echo "Installed. rdx starts in SHADOW MODE, enforced by the default config"
    echo "rather than by convention: it evaluates every prompt and logs the"
    echo "decision, but injects nothing until you set RDX_SHADOW=0."
    echo ""
    echo "Thresholds ship calibrated (min_score 0.60: precision 1.00, recall"
    echo "0.70 on a 68-case corpus). Refine them with your own history below."
    echo ""
    echo "Next:"
    echo "  rdx sync                 # build the index (~3 min for a full crawl)"
    echo "  rdx scan                 # exclude what you already have installed"
    echo "  rdx mine                 # build the gate corpus from your transcripts"
    echo "  rdx eval --all           # safety + discovery + gate"
    echo "  rdx search \"is there an mcp for linear\"   # see a would-be envelope"
    echo ""
    echo "Then set RDX_MIN_SCORE / RDX_MIN_MARGIN from \`rdx stats\` and flip"
    echo "RDX_SHADOW=0 in your shell profile to go live."
}

uninstall_discovery() {
    unregister_hook UserPromptSubmit "$SUGGEST_HOOK"
    unregister_hook PostToolUse "$OBSERVE_HOOK"
    if command -v jq >/dev/null 2>&1 && [ -f "$SETTINGS_FILE" ]; then
        local tmp
        tmp=$(mktemp)
        if jq 'del(.statusLine)' "$SETTINGS_FILE" > "$tmp"; then
            mv "$tmp" "$SETTINGS_FILE"
            echo "✓ removed statusline"
        else
            rm -f "$tmp"
        fi
    fi
    [ -L "$HOME/.local/bin/rdx" ] && rm "$HOME/.local/bin/rdx" && echo "✓ removed rdx shim"
    echo ""
    echo "rdx hooks removed. The index at $RDX_STATE was left in place;"
    echo "delete it manually if you want the data gone too."
}

if [ "${1:-}" = "--project" ]; then
    install_hook_into_project "${2:-}"
    exit 0
fi

if [ "${1:-}" = "--plugins" ]; then
    install_plugins "${2:-}"
    exit 0
fi

case "${1:-}" in
    --discovery)           DISCOVERY_MODE=install ;;
    --discovery-uninstall) DISCOVERY_MODE=uninstall ;;
    --discovery-off)
        mkdir -p "$HOME/.claude/rdx"
        touch "$HOME/.claude/rdx/DISABLED"
        echo "✓ rdx disabled (delete $HOME/.claude/rdx/DISABLED to re-enable)"
        exit 0
        ;;
esac

if [ -z "${DISCOVERY_MODE:-}" ]; then
    echo "Installing Claude_Upgrade from $REPO_DIR"
    echo ""
fi

if [ "${DISCOVERY_MODE:-}" = "uninstall" ]; then
    uninstall_discovery
    exit 0
fi

if mkdir -p "$COMMANDS_DIR"; then
    echo "✓ Ensured $COMMANDS_DIR exists"
else
    echo "✗ Failed to create $COMMANDS_DIR"
    exit 1
fi

# Global CLAUDE.md
link "$REPO_DIR/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"

# Each command file
for cmd in "$REPO_DIR/commands/"*.md; do
    [ -f "$cmd" ] || continue
    link "$cmd" "$COMMANDS_DIR/$(basename "$cmd")"
done

register_hook SessionStart "$REPO_DIR/hooks/session-start.sh"

if [ "${DISCOVERY_MODE:-}" = "install" ]; then
    install_discovery
    exit $?
fi
echo ""
if [ "$failures" -eq 0 ]; then
    echo "Done. All links in place."
    echo ""
    echo "To install the pre-push hook into a project:"
    echo "  $REPO_DIR/install.sh --project /path/to/your/project"
    echo ""
    echo "To install the recommended plugins:"
    echo "  $REPO_DIR/install.sh --plugins"
    echo ""
    echo "To install the resource-discovery system (rdx):"
    echo "  $REPO_DIR/install.sh --discovery"
else
    echo "Done with $failures failure(s). See messages above."
    exit 1
fi
