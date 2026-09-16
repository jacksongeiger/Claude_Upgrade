"""Tests for loop/tail.py — the stream-json reducer.

Run with:
    /home/user/Claude_Upgrade/discovery/venv/bin/python -m pytest loop/tests/test_tail.py -q
"""
import json
import subprocess
import sys
import time
from pathlib import Path

TAIL_PY = Path(__file__).resolve().parent.parent / "tail.py"
PRICING_JSON = Path(__file__).resolve().parent.parent / "pricing.json"

with open(PRICING_JSON) as f:
    PRICING = json.load(f)


def price(model, usage):
    m = PRICING["models"].get(model) or PRICING["models"][PRICING["aliases"].get(model, "")]
    return (
        usage.get("input_tokens", 0) / 1_000_000.0 * m["input"]
        + usage.get("output_tokens", 0) / 1_000_000.0 * m["output"]
        + usage.get("cache_read_input_tokens", 0) / 1_000_000.0 * m["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) / 1_000_000.0 * m["cache_write"]
    )


def assistant_line(model, usage, content=None, parent_tool_use_id=None):
    return json.dumps({
        "type": "assistant",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content or [{"type": "text", "text": "hello there"}],
            "usage": usage,
        },
        "parent_tool_use_id": parent_tool_use_id,
        "session_id": "sess-1",
    })


def run_tail(tmp_path, iter_n=1, stall_seconds=120, stdin_text="", timeout=10):
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    proc = subprocess.run(
        [sys.executable, str(TAIL_PY),
         "--state", str(state), "--events", str(events),
         "--pricing", str(PRICING_JSON), "--iter", str(iter_n),
         "--stall-seconds", str(stall_seconds)],
        input=stdin_text, capture_output=True, text=True, timeout=timeout,
    )
    return proc, state, events


def test_live_spend_sums_two_models():
    usage1 = {"input_tokens": 1000, "output_tokens": 500,
              "cache_read_input_tokens": 200, "cache_creation_input_tokens": 100}
    usage2 = {"input_tokens": 2000, "output_tokens": 300,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 50}
    stdin_text = (
        assistant_line("claude-sonnet-5", usage1) + "\n"
        + assistant_line("claude-opus-5", usage2) + "\n"
    )
    import tempfile, os
    with tempfile.TemporaryDirectory() as td:
        proc, state, events = run_tail(Path(td), stdin_text=stdin_text)
        assert proc.returncode == 0
        data = json.loads(state.read_text())
        expected = price("claude-sonnet-5", usage1) + price("claude-opus-5", usage2)
        assert data["live_spend_usd"] == pytest_approx(expected)
        assert data["malformed_lines"] == 0


def pytest_approx(value, rel=1e-6):
    class _Approx:
        def __eq__(self, other):
            if value == 0:
                return abs(other) < 1e-12
            return abs(other - value) / abs(value) < rel
    return _Approx()


def test_agent_tool_use_sets_exec_phase(tmp_path):
    content = [
        {"type": "text", "text": "kicking off the executor"},
        {"type": "tool_use", "id": "tu1", "name": "Task",
         "input": {"description": "run exec-sonnet on bl-014", "subagent_type": "exec-sonnet"}},
    ]
    usage = {"input_tokens": 10, "output_tokens": 5}
    stdin_text = assistant_line("claude-sonnet-5", usage, content=content) + "\n"
    proc, state, events = run_tail(tmp_path, stdin_text=stdin_text)
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    assert data["phase"] == "EXEC"


def test_reviewer_tool_use_sets_review_phase(tmp_path):
    content = [
        {"type": "tool_use", "id": "tu2", "name": "Task",
         "input": {"description": "hand off to reviewer", "subagent_type": "reviewer"}},
    ]
    usage = {"input_tokens": 10, "output_tokens": 5}
    stdin_text = assistant_line("claude-sonnet-5", usage, content=content) + "\n"
    proc, state, events = run_tail(tmp_path, stdin_text=stdin_text)
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    assert data["phase"] == "REVIEW"


def test_result_event_computes_agreement(tmp_path):
    usage = {"input_tokens": 100000, "output_tokens": 50000}
    result_line = json.dumps({
        "type": "result", "subtype": "success",
        "total_cost_usd": 1.0, "num_turns": 12, "duration_ms": 45000,
    })
    stdin_text = assistant_line("claude-sonnet-5", usage) + "\n" + result_line + "\n"
    proc, state, events = run_tail(tmp_path, stdin_text=stdin_text)
    assert proc.returncode == 0
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    assert len(lines) == 1
    final = json.loads(lines[0])
    assert final["result_cost_usd"] == 1.0
    assert final["turns"] == 12
    live = price("claude-sonnet-5", usage)
    assert final["live_spend_usd"] == pytest_approx(live)
    assert final["agreement"] == pytest_approx(abs(1.0 - live) / 1.0)
    data = json.loads(state.read_text())
    assert data["result_cost_usd"] == 1.0
    assert data["result_num_turns"] == 12
    assert data["result_duration_ms"] == 45000
    assert data["result_subtype"] == "success"


def test_malformed_line_is_counted_not_fatal(tmp_path):
    stdin_text = "{not json at all\n" + assistant_line("claude-sonnet-5", {"input_tokens": 1}) + "\n"
    proc, state, events = run_tail(tmp_path, stdin_text=stdin_text)
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    assert data["malformed_lines"] == 1


def test_stall_triggers_on_quiet_stdin(tmp_path):
    state = tmp_path / "state.json"
    events = tmp_path / "events.jsonl"
    proc = subprocess.Popen(
        [sys.executable, str(TAIL_PY),
         "--state", str(state), "--events", str(events),
         "--pricing", str(PRICING_JSON), "--iter", "1",
         "--stall-seconds", "1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(2.5)
        assert state.exists(), "tail.py should have written state.json on stall"
        data = json.loads(state.read_text())
        assert data["stall"] == "idle"
    finally:
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
    assert "STALL idle" in err


def test_subagent_message_with_parent_tool_use_id_is_priced(tmp_path):
    usage = {"input_tokens": 500, "output_tokens": 250}
    stdin_text = assistant_line("claude-sonnet-5", usage, parent_tool_use_id="tu-parent") + "\n"
    proc, state, events = run_tail(tmp_path, stdin_text=stdin_text)
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    expected = price("claude-sonnet-5", usage)
    assert data["live_spend_usd"] == pytest_approx(expected)


def test_state_preserves_foreign_keys(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"run": "loop/2026-09-16", "iter": 7, "cap_usd": 40}))
    events = tmp_path / "events.jsonl"
    stdin_text = assistant_line("claude-sonnet-5", {"input_tokens": 1, "output_tokens": 1}) + "\n"
    proc = subprocess.run(
        [sys.executable, str(TAIL_PY),
         "--state", str(state), "--events", str(events),
         "--pricing", str(PRICING_JSON), "--iter", "7"],
        input=stdin_text, capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode == 0
    data = json.loads(state.read_text())
    assert data["run"] == "loop/2026-09-16"
    assert data["cap_usd"] == 40
    assert "live_spend_usd" in data


def test_stream_txt_mirrors_assistant_text(tmp_path):
    text = "x" * 300
    content = [{"type": "text", "text": text}]
    stdin_text = assistant_line("claude-sonnet-5", {"input_tokens": 1}, content=content) + "\n"
    proc, state, events = run_tail(tmp_path, iter_n=3, stdin_text=stdin_text)
    assert proc.returncode == 0
    mirror = tmp_path / "run" / "stream-3.txt"
    assert mirror.exists()
    mirrored = mirror.read_text().strip("\n")
    assert mirrored == text[:200]


def test_hook_events_track_agents(tmp_path):
    start = json.dumps({"type": "hook_event", "hook_event_name": "SubagentStart",
                         "id": "ag-1", "subagent_type": "exec-sonnet"})
    stop = json.dumps({"type": "hook_event", "event": {"hook_event_name": "SubagentStop",
                        "id": "ag-1", "subagent_type": "exec-sonnet"}})
    # Check the running state after start alone, then after start+stop.
    proc1, state1, events1 = run_tail(tmp_path / "a", stdin_text=start + "\n")
    data1 = json.loads(state1.read_text())
    assert any(a["id"] == "ag-1" and a["type"] == "exec-sonnet" for a in data1["agents"])

    proc2, state2, events2 = run_tail(tmp_path / "b", stdin_text=start + "\n" + stop + "\n")
    data2 = json.loads(state2.read_text())
    assert data2["agents"] == []
    assert "agent_start" in events2.read_text()
    assert "agent_stop" in events2.read_text()
    log2 = events2.with_suffix(".log")
    assert log2.exists()
    assert "agent_start" in log2.read_text()
    assert "agent_stop" in log2.read_text()


def test_agents_tracked_from_real_task_started_shape(tmp_path):
    """The shape a real `claude -p --verbose` stream uses (observed on the
    first paid dryrun): system/task_started with task_id + subagent_type, and
    the agent's end is the tool_result for the Agent call in a user message.
    The documented hook_event shape never appeared."""
    import shutil
    fixture = Path(__file__).parent / "fixtures" / "stream_task_started.jsonl"
    state = tmp_path / "state.json"; state.write_text('{"iter": 1}')
    events = tmp_path / "events.jsonl"
    proc = subprocess.run(
        [sys.executable, str(TAIL_PY), "--state", str(state), "--events", str(events),
         "--pricing", str(PRICING_JSON), "--iter", "1"],
        input=fixture.read_text(), capture_output=True, text=True)
    assert proc.returncode == 0
    log = (tmp_path / "events.log").read_text()
    assert "agent_start id=abc123 type=exec-sonnet" in log
    assert "agent_stop id=abc123 type=exec-sonnet" in log
    assert json.loads(state.read_text())["agents"] == []
