#!/usr/bin/env bash
# install.sh — set up Claude Code config kit symlinks into ~/.claude/.
#
# Usage:
#   ./install.sh                           Install global kit (CLAUDE.md + commands).
#   ./install.sh --project /path/to/repo   Install the pre-push hook into that repo's .git/hooks/.
#
# Re-runnable: skips links that already point to the right place, replaces
# stale symlinks, and refuses to overwrite real files.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
COMMANDS_DIR="$CLAUDE_DIR/commands"

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

if [ "${1:-}" = "--project" ]; then
    install_hook_into_project "${2:-}"
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

echo ""
if [ "$failures" -eq 0 ]; then
    echo "Done. All links in place."
    echo ""
    echo "To install the pre-push hook into a project:"
    echo "  $REPO_DIR/install.sh --project /path/to/your/project"
else
    echo "Done with $failures failure(s). See messages above."
    exit 1
fi
