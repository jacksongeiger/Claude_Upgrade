"""Turn agents/*.md into the JSON `claude --agents` expects.

The child `claude -p` runs inside a linked worktree whose `.claude/agents/`
may not contain the kit's definitions (they are only there if committed).
Passing them on the command line makes the child independent of the state of
any checkout, which is the property that lets the driver be restarted on any
branch at any time.

Frontmatter is the restricted YAML the agent files actually use: scalars,
comma lists for tools, and a nested `hooks` block. No yaml library.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCALAR_KEYS = {"name", "description", "model", "effort", "permissionMode",
               "isolation", "memory", "color"}
INT_KEYS = {"maxTurns"}
BOOL_KEYS = {"background", "omitClaudeMd"}
LIST_KEYS = {"tools", "disallowedTools", "skills"}


def _split_frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError("no frontmatter")
    return m.group(1), m.group(2)


def _parse_hooks(lines: list[str], start: int, base_indent: int) -> tuple[dict, int]:
    """Parse the `hooks:` block. Shape:
        hooks:
          PreToolUse:
            - matcher: "Bash|Write"
              hooks:
                - type: command
                  command: "..."
                  timeout: 5
          Stop:
            - hooks:
                - type: command
                  command: "..."
    """
    hooks: dict = {}
    i = start
    event = None
    entry = None
    inner = None
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= base_indent:
            break
        s = raw.strip()
        if indent == base_indent + 2 and s.endswith(":"):
            event = s[:-1]
            hooks[event] = []
            entry = None
            inner = None
        elif indent == base_indent + 4 and s.startswith("- "):
            entry = {}
            hooks[event].append(entry)
            rest = s[2:]
            if rest.startswith("matcher:"):
                entry["matcher"] = _unquote(rest.split(":", 1)[1].strip())
            elif rest == "hooks:":
                entry["hooks"] = []
        elif indent == base_indent + 6 and s == "hooks:":
            entry["hooks"] = []
        elif indent == base_indent + 6 and s.startswith("matcher:"):
            entry["matcher"] = _unquote(s.split(":", 1)[1].strip())
        elif indent == base_indent + 8 and s.startswith("- "):
            inner = {}
            entry["hooks"].append(inner)
            k, v = s[2:].split(":", 1)
            inner[k.strip()] = _coerce(v.strip())
        elif indent == base_indent + 10 and ":" in s:
            k, v = s.split(":", 1)
            inner[k.strip()] = _coerce(v.strip())
        i += 1
    return hooks, i


def _unquote(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _coerce(v: str):
    v = _unquote(v)
    if v.isdigit():
        return int(v)
    if v in ("true", "false"):
        return v == "true"
    return v


def parse_agent(path: Path) -> tuple[str, dict]:
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    lines = fm.split("\n")
    out: dict = {"prompt": body.strip()}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if key == "hooks":
            hooks, i = _parse_hooks(lines, i + 1, 0)
            out["hooks"] = hooks
            continue
        if key in LIST_KEYS:
            out[key] = [t.strip() for t in val.split(",") if t.strip()]
        elif key in INT_KEYS:
            out[key] = int(val)
        elif key in BOOL_KEYS:
            out[key] = val == "true"
        elif key in SCALAR_KEYS:
            out[key] = _unquote(val)
        i += 1
    name = out.pop("name")
    return name, out


def build(agents_dir: Path, *, include_ui: bool = True, home: str | None = None) -> dict:
    result = {}
    for p in sorted(agents_dir.glob("*.md")):
        name, spec = parse_agent(p)
        if name == "ui-auditor" and not include_ui:
            continue
        # Hook commands in the files are written as ~/Claude_Upgrade/...; the
        # CLI does not expand ~, and the kit is not always under $HOME (this
        # sandbox has it under /home/user with HOME=/root). Rewrite to the
        # kit's real location, which is the parent of this file's directory.
        kit = str(Path(__file__).resolve().parent.parent)
        if "hooks" in spec:
            for entries in spec["hooks"].values():
                for e in entries:
                    for h in e.get("hooks", []):
                        if "command" in h:
                            h["command"] = h["command"].replace("~/Claude_Upgrade", kit)
        result[name] = spec
    return result


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents-dir", default=str(Path(__file__).resolve().parent.parent / "agents"))
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--home", default=str(Path.home()))
    a = ap.parse_args(argv)
    print(json.dumps(build(Path(a.agents_dir), include_ui=not a.no_ui, home=a.home)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
