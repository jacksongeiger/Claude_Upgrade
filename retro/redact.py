#!/usr/bin/env python3
"""redact.py — a recorded `claude -p` stream with every text field removed.

    redact.py <stream.jsonl> [--out redacted.jsonl]

Recorded streams are transcripts: assistant text, tool inputs, results. The
kit's replay corpus needs only their shape and their numbers (event types,
usage, costs, agent lifecycle, hook events) to check that tail.py's cost
meter still agrees with the bill. Principle 9: a fact from a transcript is
a count, never a quote. This script keeps the numbers and drops the words;
a test asserts no text survives.

Kept per line: type, subtype, task_id, tool_use_id, subagent_type, status, message id, thinking-token counts,
the `patch.status` of a task update, `message.model`, `message.usage`,
content blocks reduced to {type, name} (tool_use) or {type} (text, with
the text removed), result's total_cost_usd / num_turns / duration_ms /
subtype, and hook event names (hook_event_name, hook_name) without inputs.
Everything else is dropped.
"""
from __future__ import annotations

import argparse
import json
import sys

KEEP_TOP = ("type", "subtype", "task_id", "tool_use_id", "subagent_type", "status", "session_id", "model",
            "num_turns", "duration_ms", "total_cost_usd", "hook_event_name", "hook_name", "event",
            "parent_tool_use_id", "estimated_tokens", "estimated_tokens_delta")
KEEP_MESSAGE = ("id", "model", "usage", "role", "stop_reason")


def redact_obj(obj):
    out = {}
    for k in KEEP_TOP:
        if k in obj:
            v = obj[k]
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
    if isinstance(obj.get("patch"), dict) and "status" in obj["patch"]:
        out["patch"] = {"status": obj["patch"]["status"]}
    if isinstance(obj.get("message"), dict):
        m = obj["message"]
        mm = {k: m[k] for k in KEEP_MESSAGE if k in m and isinstance(m[k], (str, dict, int, float)) or (k in m and m[k] is None)}
        if isinstance(m.get("content"), list):
            blocks = []
            for c in m["content"]:
                if not isinstance(c, dict):
                    continue
                b = {"type": c.get("type")}
                if c.get("type") == "tool_use" and isinstance(c.get("name"), str):
                    b["name"] = c["name"]
                    inp = c.get("input")
                    if isinstance(inp, dict) and isinstance(inp.get("subagent_type"), str):
                        b["input"] = {"subagent_type": inp["subagent_type"]}
                blocks.append(b)
            mm["content"] = blocks
        out["message"] = mm
    if isinstance(obj.get("usage"), dict):
        out["usage"] = {k: v for k, v in obj["usage"].items() if isinstance(v, (int, float))}
    # hook events: keep the names, never the inputs or outputs
    for k in ("hook_event", "hook"):
        if isinstance(obj.get(k), dict):
            h = obj[k]
            out[k] = {kk: h[kk] for kk in ("hook_event_name", "hook_name", "name", "event") if isinstance(h.get(kk), str)}
    return out


TEXT_KEYS = {"text", "content", "input", "result", "description", "prompt", "output", "tool_input", "tool_response",
             "command", "file_path", "old_string", "new_string", "reason", "summary", "message_text", "error"}


def leaked_text(obj):
    """Any string longer than a short token under a text-bearing key, anywhere."""
    found = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in TEXT_KEYS and isinstance(v, str) and len(v) > 0:
                    found.append(path + "." + k)
                walk(v, path + "." + k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
    walk(obj, "$")
    return found


def redact_stream(lines):
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        out.append(redact_obj(obj))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="redact.py")
    ap.add_argument("stream")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    with open(a.stream, encoding="utf-8") as f:
        rows = redact_stream(f.readlines())
    leaks = [l for r in rows for l in leaked_text(r)]
    if leaks:
        print(json.dumps({"ok": False, "error": "text survived redaction", "where": leaks[:5]}))
        return 2
    text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    print(json.dumps({"ok": True, "lines": len(rows), "out": a.out}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
