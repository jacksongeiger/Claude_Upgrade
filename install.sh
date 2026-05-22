#!/usr/bin/env bash
# install.sh — set up Claude Code config kit symlinks into ~/.claude/.
#
# Usage:
#   ./install.sh                           Install global kit (CLAUDE.md + commands + SessionStart hook).
#   ./install.sh --project /path/to/repo   Install the pre-push hook into that repo's .git/hooks/.
#   ./install.sh --plugins                 Print the recommended /plugin install commands to copy into Claude Code.
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

print_plugin_commands() {
    cat <<'EOF'
Recommended plugins (run these inside Claude Code — plugin installs require Claude Code's plugin system, so they cannot be auto-run from a shell):

/plugin install feature-dev@claude-plugins-official
/plugin install commit-commands@claude-plugins-official
/plugin install playwright@claude-plugins-official
/plugin install serena@claude-plugins-official
/plugin install code-simplifier@claude-plugins-official

See PLUGINS.md in this repo for the full inventory (currently installed, recommended, and skipped).
EOF
}

if [ "${1:-}" = "--project" ]; then
    install_hook_into_project "${2:-}"
    exit 0
fi

if [ "${1:-}" = "--plugins" ]; then
    print_plugin_commands
    exit 0
fi

echo "Installing Claude_Upgrade from $REPO_DIR"
echo ""

if mkdir -p "$COMMANDS_DIR"; then
    echo "✓ Ensured $COMMANDS_DIR exists"
else
    echo "✗ Failed to create $COMMANDS_DIR"
    exit 1
fi

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

# Global CLAUDE.md
link "$REPO_DIR/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"

# Each command file
for cmd in "$REPO_DIR/commands/"*.md; do
    [ -f "$cmd" ] || continue
    link "$cmd" "$COMMANDS_DIR/$(basename "$cmd")"
done

# Register the SessionStart hook into ~/.claude/settings.json.
# Reads hooks/hooks.json, substitutes __REPO_DIR__ with the actual repo path,
# then merges the SessionStart entry into settings.json under .hooks.SessionStart.
# Dedupe-safe: any existing entry pointing at our session-start.sh is replaced.
register_session_hook() {
    local hook_script="$REPO_DIR/hooks/session-start.sh"
    local hook_template="$REPO_DIR/hooks/hooks.json"

    if ! command -v jq >/dev/null 2>&1; then
        echo "⚠ jq not available — skipping SessionStart hook registration"
        return 0
    fi
    if [ ! -f "$hook_template" ]; then
        echo "⚠ $hook_template missing — skipping SessionStart hook registration"
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
    new_entry=$(echo "$resolved" | jq '.hooks.SessionStart[0]') || {
        echo "✗ failed to extract SessionStart entry from $hook_template"
        failures=$((failures + 1))
        return 1
    }

    local tmp
    tmp=$(mktemp)
    if jq --arg cmd "$hook_script" --argjson entry "$new_entry" '
        .hooks = (.hooks // {})
        | .hooks.SessionStart = ((.hooks.SessionStart // [])
            | map(select((.hooks // []) | all(.command != $cmd))))
        | .hooks.SessionStart += [$entry]
    ' "$SETTINGS_FILE" > "$tmp"; then
        mv "$tmp" "$SETTINGS_FILE"
        echo "✓ registered SessionStart hook in $SETTINGS_FILE"
    else
        rm -f "$tmp"
        echo "✗ failed to merge SessionStart hook into $SETTINGS_FILE"
        failures=$((failures + 1))
    fi
}

register_session_hook

echo ""
if [ "$failures" -eq 0 ]; then
    echo "Done. All links in place."
    echo ""
    echo "To install the pre-push hook into a project:"
    echo "  $REPO_DIR/install.sh --project /path/to/your/project"
    echo ""
    echo "To print the recommended /plugin install commands:"
    echo "  $REPO_DIR/install.sh --plugins"
else
    echo "Done with $failures failure(s). See messages above."
    exit 1
fi
