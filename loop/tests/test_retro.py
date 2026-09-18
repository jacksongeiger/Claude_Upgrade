"""retro/: redaction keeps numbers and drops words, a redacted stream replays
through tail.py with the same bill, collect.py counts what the drivers
wrote, report.py renders it, corpus_run.py runs the labelled corpus."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RETRO = ROOT / "retro"
FIXTURE = ROOT / "loop" / "tests" / "fixtures" / "stream_task_started.jsonl"
sys.path.insert(0, str(RETRO))
import redact  # noqa: E402


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def test_redaction_drops_every_text_field_and_keeps_usage():
    lines = [
        json.dumps({"type": "assistant", "message": {"model": "claude-fable-5-1", "content": [
            {"type": "text", "text": "SECRET PLAN TEXT"},
            {"type": "tool_use", "name": "Bash", "input": {"command": "cat ~/.ssh/id_rsa"}},
            {"type": "tool_use", "name": "Agent", "input": {"subagent_type": "exec-sonnet", "prompt": "SECRET PROMPT"}}],
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}),
        json.dumps({"type": "system", "subtype": "task_started", "task_id": "t1", "description": "SECRET DESCRIPTION", "subagent_type": "exec-sonnet"}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "SECRET RESULT"}]}}),
        json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 1.25, "num_turns": 3, "result": "SECRET SUMMARY"}),
    ]
    rows = redact.redact_stream(lines)
    text = json.dumps(rows)
    assert "SECRET" not in text and "id_rsa" not in text
    assert rows[0]["message"]["usage"]["input_tokens"] == 100
    assert rows[0]["message"]["content"][1] == {"type": "tool_use", "name": "Bash"}
    assert rows[0]["message"]["content"][2]["input"] == {"subagent_type": "exec-sonnet"}
    assert rows[1]["subagent_type"] == "exec-sonnet" and "description" not in rows[1]
    assert rows[3]["total_cost_usd"] == 1.25
    assert all(not redact.leaked_text(r) for r in rows)


def test_redacted_stream_replays_with_the_same_bill(tmp_path):
    src = tmp_path / "stream.jsonl"
    src.write_text(FIXTURE.read_text())
    out = tmp_path / "redacted.jsonl"
    p = sh([sys.executable, str(RETRO / "redact.py"), str(src), "--out", str(out)])
    assert p.returncode == 0, p.stdout + p.stderr
    def tail(stream):
        d = tmp_path / stream.stem; d.mkdir(exist_ok=True)
        return sh([sys.executable, str(ROOT / "loop" / "tail.py"), "--state", str(d / "state.json"), "--events", str(d / "events.jsonl"),
                   "--pricing", str(ROOT / "loop" / "pricing.json"), "--iter", "1"], input=stream.read_text())
    a, b = tail(src), tail(out)
    fa = json.loads(a.stdout.strip().splitlines()[-1]); fb = json.loads(b.stdout.strip().splitlines()[-1])
    assert fa["result_cost_usd"] == fb["result_cost_usd"]
    assert abs(float(fa["live_spend_usd"]) - float(fb["live_spend_usd"])) < 1e-6


def test_collect_counts_a_synthetic_project(tmp_path):
    p = tmp_path / "proj"; (p / ".pipeline" / "build").mkdir(parents=True); (p / ".pipeline" / "validate" / "ab12cd34").mkdir(parents=True)
    (p / ".pipeline" / "ux" / "t-1").mkdir(parents=True); (p / ".pipeline" / "ship" / "v1").mkdir(parents=True); (p / ".loop").mkdir()
    (p / ".pipeline" / "ledger.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"stage": "build", "id": "m1", "cost_usd": 4.0}, {"stage": "build", "id": "m2", "cost_usd": 2.0},
        {"stage": "validate:ab12cd34:author", "cost_usd": 0.5}, {"stage": "classify:dimension", "cost_usd": 0.01}]))
    (p / ".pipeline" / "build" / "state.json").write_text(json.dumps({"milestones": {"m1": {"status": "done"}, "m2": {"status": "done"}, "m3": {"status": "blocked"}},
                                                                     "attempts": {"m1": 1, "m2": 3, "m3": 2}}))
    (p / ".pipeline" / "validate" / "ab12cd34" / "verdict.json").write_text(json.dumps({"verdict": "GO", "overruled": {"by": "x"}}))
    (p / ".pipeline" / "validate" / "ab12cd34" / "feedback-check.json").write_text(json.dumps({"contradictions": [{"bullet": "b", "anchor": "a"}]}))
    (p / ".pipeline" / "events.log").write_text("2026 HUMAN_OVERRIDE kind=screenshot-baseline what=home\n2026 HUMAN_OVERRIDE kind=validation-overrule what=ab12cd34\n")
    (p / ".pipeline" / "ux" / "t-1" / "score.json").write_text(json.dumps({"completed": True}))
    (p / ".pipeline" / "ux" / "t-1" / "findings.json").write_text(json.dumps({"dead_ends": ["x"], "confusions": []}))
    (p / ".pipeline" / "ship" / "v1" / "ship-report.json").write_text(json.dumps({"checks": [{"name": "gates", "ok": False}, {"name": "secrets", "ok": True}]}))
    (p / ".loop" / "scores.jsonl").write_text("".join(json.dumps(r) + "\n" for r in [
        {"iter": 0, "outcome": "baseline", "cost_usd": 0}, {"iter": 1, "outcome": "kept", "cost_usd": 2.0}, {"iter": 2, "outcome": "reset-flat", "cost_usd": 1.5}]))
    (p / ".loop" / "state.json").write_text(json.dumps({"stop_reason": "cap"}))
    (p / ".loop" / "events.log").write_text("x STOP reason=cap\ny DENY safety git push\nz DRYRUN cost agreement: result=2 live=2.1 agreement=0.05 ok=true\n")
    (p / ".loop" / "backlog.yaml").write_text("rows:\n  - id: fb-1\n    title: slow\n    dimension: perf\n    status: open\n  - id: ux-1\n    title: t\n    status: done\n    note: 'x; resolved: clean walkthrough r (judged)'\n")
    out = tmp_path / "facts.json"
    r = sh([sys.executable, str(RETRO / "collect.py"), "--project", str(p), "--out", str(out), "--label", "test"])
    assert r.returncode == 0, r.stderr
    f = json.loads(out.read_text())
    pr = f["projects"][0]
    assert pr["build"] == {"milestones": 3, "attempts_total": 6, "accepted_first_try": 1, "accepted_after_retry": 1, "blocked": 1,
                           "cost_usd": 6.0, "cost_per_accepted_milestone": 3.0}
    assert pr["validation"]["verdicts"] == {"GO": 1} and pr["validation"]["overrules"] == 1 and pr["validation"]["feedback_contradictions"] == 1
    assert pr["nightshift"]["kept"] == 1 and pr["nightshift"]["reset_flat"] == 1 and pr["nightshift"]["denies"] == {"safety": 1}
    assert pr["nightshift"]["stop_reasons"] == {"cap": 1} and pr["nightshift"]["agreement_max_abs"] == 0.05
    assert pr["persona"] == {"walkthroughs": 1, "completed": 1, "findings": 1, "findings_deduped": 0, "rows_resolved_judged": 1, "rows_resolved_rule": 0}
    assert pr["ship"]["rows_failed"] == {"gates": 1} and pr["human_overrides"] == {"screenshot-baseline": 1, "validation-overrule": 1}
    assert f["totals"]["first_try_rate"] == 0.5 and f["totals"]["cost_per_accepted_milestone"] == 3.0
    # the report renders it and, with two facts files, a trend
    out2 = tmp_path / "facts2.json"; sh([sys.executable, str(RETRO / "collect.py"), "--project", str(p), "--out", str(out2), "--label", "test2"])
    md = tmp_path / "RETRO.md"
    r = sh([sys.executable, str(RETRO / "report.py"), "--facts", str(out), str(out2), "--out", str(md)])
    assert r.returncode == 0 and "## Trend" in md.read_text() and "cost per accepted milestone" in md.read_text()


def test_corpus_runs_and_passes():
    r = sh([sys.executable, str(RETRO / "corpus_run.py"), "--json"], cwd=str(ROOT))
    data = json.loads(r.stdout.strip().splitlines()[-1])
    assert data["failed"] == 0, data
    assert data["passed"] >= 5
