#!/usr/bin/env bash
# install.sh — set up Claude Code config kit symlinks into ~/.claude/
# Re-runnable: skips links that already point to the right place, replaces
# stale symlinks, and refuses to overwrite real files.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="$HOME/.claude"
COMMANDS_DIR="$CLAUDE_DIR/commands"

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

# Skills folder (only if it still exists)
if [ -d "$REPO_DIR/skills" ]; then
    link "$REPO_DIR/skills" "$CLAUDE_DIR/skills"
fi

echo ""
if [ "$failures" -eq 0 ]; then
    echo "Done. All links in place."
else
    echo "Done with $failures failure(s). See messages above."
    exit 1
fi
