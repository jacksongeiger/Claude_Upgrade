"""UserPromptSubmit hook entrypoint.

Contract, and every clause of it matters:

  * stdin  : the hook payload JSON (prompt, session_id, cwd, ...)
  * stdout : either `{}` or a UserPromptSubmit hookSpecificOutput object
  * exit   : ALWAYS 0

`additionalContext` MUST be nested inside `hookSpecificOutput`. A top-level
field is silently ignored, which is the worst possible failure mode: it looks
correct and does nothing. test_hook.py asserts the exact shape.

Exit code 2 would BLOCK the user's prompt. This hook must never be able to do
that under any failure mode, so every path returns 0 - including unparseable
stdin, a missing database, a corrupt database, and an unexpected exception.
"""

from __future__ import annotations

import json
import sys
import traceback


def build_output(context: str | None) -> dict:
    if not context:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def main(argv: list[str] | None = None) -> int:
    try:
        raw = sys.stdin.read()
    except Exception:
        print("{}")
        return 0

    try:
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except ValueError:
        print("{}")
        return 0

    try:
        from . import config, db, retrieve

        cfg = config.load_config()
        if cfg.disabled:
            print("{}")
            return 0
        if not cfg.db_path.exists():
            print("{}")
            return 0

        conn = db.open_db(cfg.db_path, busy_timeout_ms=50)
        try:
            decision = retrieve.suggest(payload, conn, cfg=cfg)
        finally:
            conn.close()

        print(json.dumps(build_output(decision.context if decision.inject else None)))
    except Exception:
        # Diagnostics go to stderr (captured into rdx.log by the shim); stdout
        # stays a valid no-op so the prompt is never disturbed.
        traceback.print_exc(file=sys.stderr)
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
