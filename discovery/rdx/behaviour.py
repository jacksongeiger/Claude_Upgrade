"""Behavioural eval: does the model ACT on the envelope?

Every other corpus tests plumbing — the hook fires, the gate is silent, the
right row ranks. This one tests the only thing that ultimately decides whether
the project is worth anything: a real model, with no knowledge of rdx, receives
a real envelope through the real hook, and either uses it or does not.

It is the only test class that catches framing failures. The first envelope
header passed every unit test and was rejected by the first model that read it
("I'd treat them as unverified before installing anything from that source"),
which is the exact failure mode rdx exists to fix, one layer up.

How it works, and why it is shaped this way:

  * It registers the real hook in the real settings file, runs prompts through
    a headless `claude -p`, and restores settings in a finally block. Feeding
    the envelope in as prompt text instead would be tidier and would test the
    wrong thing — `additionalContext` arrives by a different path than user
    text, and that difference is the whole point.

  * It is NOT part of `rdx eval --all`. It costs real model calls, takes
    minutes rather than seconds, and mutates a settings file. Opt in with
    `rdx eval --behaviour`.

  * It checks three outcomes, not two. "Surfaced" and "ignored" are the
    obvious ones; "rejected" is the third, and it is the one worth alarming on,
    because a model that argues with the index is worse than one that quietly
    ignores it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, db
from .evalharness import EvalResult, _load_yaml

CLAUDE_TIMEOUT_S = 300

# Language that means the model read the block and argued with it. This is the
# specific failure the first envelope produced, so it gets detected by name
# rather than being lumped in with "did not mention".
REJECTION_PATTERNS = [
    re.compile(r"haven'?t verified", re.I),
    re.compile(r"\bunverified\b", re.I),
    re.compile(r"treat (?:them|it|these) as (?:unverified|untrusted|suspect)", re.I),
    re.compile(r"from that source", re.I),
    re.compile(r"(?:can'?t|cannot|don'?t) (?:vouch|confirm) for", re.I),
    re.compile(r"\bnot sure what `?rdx`? is", re.I),
    re.compile(r"unfamiliar with (?:the )?`?rdx", re.I),
]


@dataclass
class CaseResult:
    case_id: str
    fired: bool
    expected_fire: bool
    surfaced: bool
    rejected: bool
    output: str = ""
    error: str | None = None

    @property
    def verdict(self) -> str:
        if self.error:
            return "ERROR"
        if self.fired != self.expected_fire:
            return "GATE"          # gate behaved differently than expected
        if not self.expected_fire:
            return "PASS"          # correctly silent
        if self.rejected:
            return "REJECTED"      # the framing failure
        return "PASS" if self.surfaced else "IGNORED"


@dataclass
class BehaviourReport:
    results: list[CaseResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.verdict] = out.get(r.verdict, 0) + 1
        return out


def claude_available() -> bool:
    return shutil.which("claude") is not None


def _settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _hook_script(repo_dir: Path) -> Path:
    """Materialize the shim with __REPO_DIR__ resolved, in a temp file.

    The checked-in shim carries a placeholder that install.sh substitutes; the
    eval must not depend on rdx already being installed.
    """
    src = repo_dir / "hooks" / "resource-suggest.sh"
    text = src.read_text(encoding="utf-8").replace("__REPO_DIR__", str(repo_dir))
    fd, path = tempfile.mkstemp(prefix="rdx-behaviour-hook-", suffix=".sh")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    Path(path).chmod(0o755)
    return Path(path)


def _register(hook: Path) -> str:
    settings = _settings_path()
    settings.parent.mkdir(parents=True, exist_ok=True)
    original = settings.read_text(encoding="utf-8") if settings.exists() else "{}"

    try:
        data = json.loads(original)
    except ValueError:
        data = {}
    data.setdefault("hooks", {})["UserPromptSubmit"] = [
        {"hooks": [{"type": "command", "command": str(hook),
                    "async": False, "timeout": 15}]}
    ]
    settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return original


def _restore(original: str) -> None:
    _settings_path().write_text(original, encoding="utf-8")


def _last_injection(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    row = conn.execute(
        "SELECT n_shown, suppressed_reason FROM injection ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return False, "no-row"
    return bool(row["n_shown"]), row["suppressed_reason"]


def run_behaviour(*, repo_dir: Path | None = None, limit: int | None = None,
                  corpora_dir: Path | None = None,
                  verbose: bool = False) -> BehaviourReport:
    repo_dir = repo_dir or config.PROJECT_DIR.parent
    cases = _load_yaml((corpora_dir or config.CORPORA_DIR) / "behaviour.yaml"
                       ).get("cases", [])
    if limit:
        cases = cases[:limit]

    report = BehaviourReport()
    hook = _hook_script(repo_dir)
    original = _register(hook)

    env = {**os.environ, "RDX_SHADOW": "0",
           # Per-case sessions would still share the snooze window, which would
           # silently suppress later cases and look like a framing failure.
           "RDX_STATE_DIR": str(config.STATE_DIR)}

    try:
        for case in cases:
            cid = str(case.get("id", "?"))
            prompt = str(case.get("prompt", ""))
            expected_fire = bool(case.get("expect_fire"))

            # Clear per-session budget state so each case is independent.
            with db.open_db(config.DB_PATH) as conn:
                conn.executescript(
                    "DELETE FROM injection_item; DELETE FROM injection;")
                conn.commit()

            try:
                proc = subprocess.run(
                    ["claude", "-p", prompt], capture_output=True, text=True,
                    timeout=CLAUDE_TIMEOUT_S, env=env, cwd=tempfile.gettempdir())
                output = (proc.stdout or "") + (proc.stderr or "")
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                report.results.append(CaseResult(
                    cid, False, expected_fire, False, False, error=str(exc)))
                continue

            with db.open_db(config.DB_PATH, readonly=True) as conn:
                fired, _reason = _last_injection(conn)

            mentions = [str(m).lower() for m in (case.get("expect_mentions") or [])]
            lowered = output.lower()
            surfaced = any(m in lowered for m in mentions) if mentions else False
            rejected = any(p.search(output) for p in REJECTION_PATTERNS)

            result = CaseResult(cid, fired, expected_fire, surfaced, rejected,
                                output=output)
            report.results.append(result)
            if verbose:
                print(f"  [{result.verdict:<8}] {cid}")
    finally:
        _restore(original)
        hook.unlink(missing_ok=True)

    return report


def to_eval_result(report: BehaviourReport) -> EvalResult:
    res = EvalResult("behaviour")
    counts = report.counts()
    res.passed = counts.get("PASS", 0)
    res.failed = (counts.get("REJECTED", 0) + counts.get("IGNORED", 0)
                  + counts.get("GATE", 0))
    res.skipped = counts.get("ERROR", 0)

    fired_cases = [r for r in report.results if r.expected_fire and not r.error]
    if fired_cases:
        surfaced = sum(1 for r in fired_cases if r.surfaced)
        res.metrics["surfaced_rate"] = round(surfaced / len(fired_cases), 3)
        res.metrics["rejected"] = sum(1 for r in fired_cases if r.rejected)

    for r in report.results:
        if r.verdict == "REJECTED":
            res.failures.append(
                f"{r.case_id}: model argued with the index — this is a FRAMING "
                f"failure, not a retrieval one")
        elif r.verdict == "IGNORED":
            res.failures.append(
                f"{r.case_id}: envelope fired but the model never mentioned it")
        elif r.verdict == "GATE":
            res.failures.append(
                f"{r.case_id}: expected fire={r.expected_fire}, got {r.fired}")
        elif r.verdict == "ERROR":
            res.notes.append(f"{r.case_id}: {r.error}")
    return res
