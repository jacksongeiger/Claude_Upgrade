"""Mine real prompts from local Claude Code transcripts.

Verified shape: ~/.claude/projects/<cwd-slug>/<session-uuid>.jsonl, one JSON
object per line. Real user prompts are entries with type == "user" whose
message.content is a STRING; tool results appear in the same position as a
LIST, so they filter out cleanly.

This is what replaces waiting a week for shadow data: the gate can be
calibrated against hundreds of prompts the user has actually typed, today.

Privacy: mined prompts are written to corpora/gate.yaml, which is gitignored.
They are the user's own words and must never be committed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Prompts that are structurally uninteresting for gate calibration.
_SKIP_PREFIXES = ("/", "<", "[Request interrupted")
_SKIP_MARKERS = (
    "<command-name>", "<local-command", "<system-reminder>",
    "Caveat: The messages below", "<task-notification>", "<wake ",
)


@dataclass
class MinedPrompt:
    text: str
    cwd: str | None
    session_id: str | None
    timestamp: str | None


def _iter_transcript_files(root: Path | None = None) -> Iterator[Path]:
    root = root or PROJECTS_DIR
    if not root.exists():
        return
    # Top-level session transcripts only; subagent transcripts under
    # <session>/subagents/ are model-to-model traffic, not user prompts.
    yield from sorted(root.glob("*/*.jsonl"))


def _extract(line: str) -> MinedPrompt | None:
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return None

    message = entry.get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        return None  # tool-result arrays land here

    text = content.strip()
    if not text or text.startswith(_SKIP_PREFIXES):
        return None
    if any(marker in text for marker in _SKIP_MARKERS):
        return None
    return MinedPrompt(
        text=text,
        cwd=entry.get("cwd"),
        session_id=entry.get("sessionId"),
        timestamp=entry.get("timestamp"),
    )


def mine(root: Path | None = None, *, min_chars: int = 25,
         max_chars: int = 2000) -> list[MinedPrompt]:
    """Return deduplicated real prompts, newest transcripts last."""
    seen: set[str] = set()
    out: list[MinedPrompt] = []
    for path in _iter_transcript_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            mined = _extract(line)
            if mined is None:
                continue
            if not (min_chars <= len(mined.text) <= max_chars):
                continue
            key = re.sub(r"\s+", " ", mined.text.lower())[:200]
            if key in seen:
                continue
            seen.add(key)
            out.append(mined)
    return out


def heuristic_label(text: str) -> str:
    """Pre-label a prompt to make human review fast.

    This is a STARTING POINT, not ground truth: the whole point of labelling is
    to correct where this disagrees with judgement. It deliberately reuses the
    gate's own intent regex so the corpus measures the gate rather than a
    parallel reimplementation of it.
    """
    from .retrieve import INTENT_RE

    return "inject" if INTENT_RE.search(text) else "silent"


def to_yaml(prompts: list[MinedPrompt], *, prelabel: bool = True) -> str:
    """Serialize to corpora/gate.yaml.

    Hand-rolled rather than yaml.dump so prompts stay readable as block
    scalars, which matters when a human is labelling a few hundred of them.
    """
    lines = [
        "# Gate calibration corpus - REAL prompts mined from local transcripts.",
        "#",
        "# label: inject  -> a suggestion here would have been welcome",
        "#        silent  -> a suggestion here would have been noise",
        "#        unknown -> not yet reviewed (excluded from scoring)",
        "#",
        "# Pre-labels come from the gate's own intent regex and WILL be wrong.",
        "# Correcting the disagreements is the point.",
        "#",
        "# PRIVACY: these are your own prompts. This file is gitignored.",
        "",
        "cases:",
    ]
    for p in prompts:
        label = heuristic_label(p.text) if prelabel else "unknown"
        text = p.text.replace("\r", "")
        lines.append(f"  - label: {label}")
        lines.append("    text: |-")
        for row in text.split("\n"):
            lines.append(f"      {row}")
        if p.cwd:
            lines.append(f"    cwd: {json.dumps(p.cwd)}")
        lines.append("")
    return "\n".join(lines)
