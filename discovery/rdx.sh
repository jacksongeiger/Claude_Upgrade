#!/usr/bin/env bash
# rdx launcher.
#
# Resolves the venv AND the package root relative to this script, so the CLI
# works from any directory — including from the statusline, which Claude Code
# invokes with an unpredictable cwd.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$HERE/venv/bin/python" -m rdx "$@"
