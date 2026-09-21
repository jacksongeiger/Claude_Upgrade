#!/usr/bin/env bash
# rdx launcher.
#
# Resolves the venv AND the package root relative to this script, so the CLI
# works from any directory — including from the statusline, which Claude Code
# invokes with an unpredictable cwd.
set -euo pipefail
# BASH_SOURCE is the symlink (~/.local/bin/rdx) when launched from PATH; resolve it or the venv is looked for next to the link.
SELF="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "${BASH_SOURCE[0]}")"
HERE="$(cd "$(dirname "$SELF")" && pwd)"
export PYTHONPATH="$HERE${PYTHONPATH:+:$PYTHONPATH}"
exec "$HERE/venv/bin/python" -m rdx "$@"
