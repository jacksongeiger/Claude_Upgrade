#!/usr/bin/env python3
"""Nightshift stream reducer.

python3 tail.py --state <state.json> --events <events.jsonl> --pricing <pricing.json> \
    --iter N [--stall-seconds 120]

Reads a `claude -p --output-format stream-json --include-hook-events
--forward-subagent-text --verbose` transcript line by line from STDIN and
keeps state.json's tail.py-owned keys (live_spend_usd, phase, agents, stall,
malformed_lines, result_*) up to date as lines arrive — it does not wait for
EOF. See loop/README.md ("state.json", "Cost accounting", "events.jsonl") for
the contract this implements.

Assumed stream-json shapes (undocumented upstream; see final report for the
caveats):

  assistant message:
    {"type": "assistant",
     "message": {"model": "...", "usage": {"input_tokens": N, ...},
                 "content": [{"type": "text", "text": "..."},
                              {"type": "tool_use", "name": "...", "input": {...}}]},
     "parent_tool_use_id": null | "..."}

  result event:
    {"type": "result", "subtype": "success", "total_cost_usd": 1.23,
     "num_turns": 7, "duration_ms": 45000, ...}

  hook event (shape unconfirmed — handled both ways):
    {"type": "hook_event", "hook_event_name": "SubagentStart", "id": "...", ...}
    {"type": "hook_event", "event": {"hook_event_name": "SubagentStart", "id": "...", ...}}
"""
import argparse
import json
import os
import select
import sys
import tempfile
from datetime import datetime, timezone

AGENT_TOOL_NAMES = ("agent", "task")
PERMISSION_WORDS = ("permission", "approve", "needs approval")


# ---------------------------------------------------------------- utilities

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError):
        return None


def atomic_write_json(path, data):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tail_tmp_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_line(path, line):
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    with open(path, "a") as f:
        f.write(line + "\n")


def log_path_for(events_path):
    base, ext = os.path.splitext(events_path)
    if ext == ".jsonl":
        return base + ".log"
    return events_path + ".log"


def stream_txt_path_for(events_path, iter_n):
    d = os.path.dirname(os.path.abspath(events_path)) or "."
    # Under run/ so it is gitignored with the rest of the runtime state and
    # where status.py --tail looks for it.
    run_dir = os.path.join(d, "run")
    os.makedirs(run_dir, exist_ok=True)
    return os.path.join(run_dir, "stream-%s.txt" % iter_n)


def mentions_permission(text):
    if not text:
        return False
    t = text.lower()
    return any(w in t for w in PERMISSION_WORDS)


# ------------------------------------------------------------------ pricing

def load_pricing(path):
    with open(path) as f:
        return json.load(f)


def resolve_model_pricing(pricing, model):
    models = pricing.get("models", {}) or {}
    aliases = pricing.get("aliases", {}) or {}
    fallback = pricing.get("fallback", {}) or {}
    if not model:
        return fallback
    if model in models:
        return models[model]
    if model in aliases and aliases[model] in models:
        return models[aliases[model]]
    # tolerate dated/suffixed model ids (e.g. a snapshot tag not yet in the
    # table) by prefix-matching against known keys.
    for key in models:
        if model.startswith(key) or key in model:
            return models[key]
    return fallback


def price_usage(pricing, model, usage):
    p = resolve_model_pricing(pricing, model)
    usage = usage or {}
    inp = usage.get("input_tokens") or 0
    out = usage.get("output_tokens") or 0
    cr = usage.get("cache_read_input_tokens") or 0
    cw = usage.get("cache_creation_input_tokens") or 0
    return (
        (inp / 1_000_000.0) * p.get("input", 0)
        + (out / 1_000_000.0) * p.get("output", 0)
        + (cr / 1_000_000.0) * p.get("cache_read", 0)
        + (cw / 1_000_000.0) * p.get("cache_write", 0)
    )


# --------------------------------------------------------------- hook event

def get_hook_info(obj):
    """Return (hook_event_name, payload_dict) or (None, None).

    Handles both a top-level event carrying hook_event_name, and one nested
    under an "event" key — the exact upstream shape is undocumented.
    """
    name = obj.get("hook_event_name")
    if name:
        return name, obj
    nested = obj.get("event")
    if isinstance(nested, dict):
        name = nested.get("hook_event_name")
        if name:
            return name, nested
    return None, None


def hook_agent_id(payload, fallback_idx):
    return (
        payload.get("id")
        or payload.get("agent_id")
        or payload.get("subagent_id")
        or payload.get("tool_use_id")
        or payload.get("session_id")
        or ("agent-%d" % fallback_idx)
    )


def hook_agent_type(payload):
    return (
        payload.get("subagent_type")
        or payload.get("agent_type")
        or payload.get("type")
        or payload.get("name")
        or "unknown"
    )


# ------------------------------------------------------------------- driver

class Reducer:
    def __init__(self, args, pricing):
        self.args = args
        self.pricing = pricing
        self.state_path = args.state
        self.events_path = args.events
        self.log_path = log_path_for(args.events)
        self.stream_txt_path = stream_txt_path_for(args.events, args.iter)

        disk = read_json(self.state_path) or {}
        self.live_spend = float(disk.get("live_spend_usd") or 0.0)
        self.phase = disk.get("phase")
        agents_list = disk.get("agents")
        self.agents = {}
        if isinstance(agents_list, list):
            for a in agents_list:
                if isinstance(a, dict) and a.get("id") is not None:
                    self.agents[a["id"]] = a
        self.stall = disk.get("stall")
        self.malformed_lines = int(disk.get("malformed_lines") or 0)
        self.result_fields = {}
        self.last_assistant_text = ""
        self._agent_seq = 0

    # ---- state.json (read-modify-write, own keys only) ----

    def write_state(self):
        current = read_json(self.state_path) or {}
        current["live_spend_usd"] = round(self.live_spend, 8)
        if self.phase is not None:
            current["phase"] = self.phase
        current["agents"] = list(self.agents.values())
        current["stall"] = self.stall
        current["malformed_lines"] = self.malformed_lines
        current.update(self.result_fields)
        atomic_write_json(self.state_path, current)

    # ---- events.jsonl / .log ----

    def emit_agent_event(self, event_name, agent_id, agent_type, ts):
        append_line(self.events_path, json.dumps(
            {"event": event_name, "id": agent_id, "type": agent_type, "ts": ts}
        ))
        append_line(self.log_path, "%s %s id=%s type=%s" % (ts, event_name, agent_id, agent_type))

    # ---- stream mirror ----

    def mirror_text(self, text):
        append_line(self.stream_txt_path, text[:200])

    # ---- per-line handlers ----

    def handle_assistant(self, obj):
        message = obj.get("message") or {}
        model = message.get("model")
        usage = message.get("usage") or {}
        self.live_spend += price_usage(self.pricing, model, usage)

        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text = block.get("text") or ""
                self.last_assistant_text = text
                self.mirror_text(text)
            elif btype == "tool_use":
                self.apply_phase(block)

    def apply_phase(self, tool_use_block):
        name = (tool_use_block.get("name") or "").lower()
        input_ = tool_use_block.get("input") or {}
        if name in AGENT_TOOL_NAMES:
            subagent_type = input_.get("subagent_type")
            description = str(input_.get("description") or "")
            if subagent_type == "reviewer" or "reviewer" in description.lower():
                self.phase = "REVIEW"
            else:
                self.phase = "EXEC"
        elif name == "bash":
            command = str(input_.get("command") or "")
            if "merge.sh" in command:
                self.phase = "MERGE"
        elif name == "write":
            path = str(input_.get("file_path") or input_.get("path") or "")
            if "summary.md" in path:
                self.phase = "CLOSE"

    def handle_hook_event(self, obj):
        name, payload = get_hook_info(obj)
        if name not in ("SubagentStart", "SubagentStop"):
            return False
        self._agent_seq += 1
        agent_id = hook_agent_id(payload, self._agent_seq)
        agent_type = hook_agent_type(payload)
        ts = now_iso()
        if name == "SubagentStart":
            self.agents[agent_id] = {"id": agent_id, "type": agent_type, "since": ts}
            self.emit_agent_event("agent_start", agent_id, agent_type, ts)
        else:
            self.agents.pop(agent_id, None)
            self.emit_agent_event("agent_stop", agent_id, agent_type, ts)
        return True

    def handle_result(self, obj):
        total_cost = obj.get("total_cost_usd")
        subtype = obj.get("subtype")
        num_turns = obj.get("num_turns")
        duration_ms = obj.get("duration_ms")
        self.result_fields = {
            "result_cost_usd": total_cost,
            "result_subtype": subtype,
            "result_num_turns": num_turns,
            "result_duration_ms": duration_ms,
        }
        if total_cost:
            agreement = abs(total_cost - self.live_spend) / total_cost
        else:
            agreement = None
        return {
            "result_cost_usd": total_cost,
            "live_spend_usd": round(self.live_spend, 8),
            "agreement": agreement,
            "turns": num_turns,
            "stall": self.stall,
        }

    def handle_line(self, raw_line):
        """Returns True if state.json should be rewritten."""
        line = raw_line.strip()
        if not line:
            return False
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("top-level JSON value is not an object")
        except (ValueError, TypeError):
            self.malformed_lines += 1
            return True

        typ = obj.get("type")
        if typ == "assistant":
            self.handle_assistant(obj)
            return True
        if typ == "result":
            final = self.handle_result(obj)
            self.write_state()
            print(json.dumps(final))
            sys.stdout.flush()
            sys.exit(0)
        hook_name, _ = get_hook_info(obj)
        if hook_name is not None:
            return self.handle_hook_event(obj)
        return False

    def on_stall(self):
        kind = "permission" if mentions_permission(self.last_assistant_text) else "idle"
        self.stall = kind
        self.write_state()
        print("STALL %s" % kind, file=sys.stderr)
        sys.stderr.flush()

    def run(self):
        stall_seconds = self.args.stall_seconds
        stalled = False
        while True:
            try:
                ready, _, _ = select.select([sys.stdin], [], [], stall_seconds)
            except (OSError, ValueError):
                break
            if not ready:
                if not stalled:
                    stalled = True
                    self.on_stall()
                continue
            raw_line = sys.stdin.readline()
            if raw_line == "":
                break  # EOF
            stall_cleared = False
            if stalled:
                stalled = False
                self.stall = None
                stall_cleared = True
            changed = self.handle_line(raw_line)
            if changed or stall_cleared:
                self.write_state()
        # EOF without a result event: flush final state and stop.
        self.write_state()
        sys.exit(0)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state", required=True)
    p.add_argument("--events", required=True)
    p.add_argument("--pricing", required=True)
    p.add_argument("--iter", required=True)
    p.add_argument("--stall-seconds", type=float, default=120)
    return p.parse_args()


def main():
    args = parse_args()
    try:
        pricing = load_pricing(args.pricing)
    except (OSError, json.JSONDecodeError):
        pricing = {"models": {}, "aliases": {}, "fallback": {
            "input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25}}
    reducer = Reducer(args, pricing)
    reducer.run()


if __name__ == "__main__":
    main()
