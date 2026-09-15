"""Paths, thresholds and feature flags.

Runtime state deliberately lives outside the git repo (see README): a WAL-mode
SQLite index inside a working tree means `git status` noise and accidental
commits of a multi-megabyte binary.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

STATE_DIR = Path(os.environ.get("RDX_STATE_DIR", Path.home() / ".claude" / "rdx"))
DB_PATH = STATE_DIR / "index.db"
LOG_PATH = STATE_DIR / "rdx.log"
SPOOL_PATH = STATE_DIR / "toolevents.jsonl"
DISABLED_FLAG = STATE_DIR / "DISABLED"

PKG_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PKG_DIR.parent
CORPORA_DIR = PROJECT_DIR / "corpora"
FIXTURES_DIR = PROJECT_DIR / "tests" / "fixtures"

SCHEMA_VERSION = 1

# Sanitizer limits. These are hard caps, not suggestions: anything longer is
# truncated or dropped before it can reach a model's context.
MAX_NAME_LEN = 64
MAX_SUMMARY_LEN = 160
MAX_URL_LEN = 300
MAX_TAGS = 20
MAX_TAG_LEN = 24
MIN_DESCRIPTION_LEN = 20  # ingest-time floor; shorter descriptions carry no signal


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    """Runtime knobs. Thresholds start at +inf so the gate is silent until
    calibrated against real data by `rdx eval --gate`."""

    db_path: Path = DB_PATH
    shadow: bool = False
    disabled: bool = False

    min_score: float = float("inf")
    min_margin: float = float("inf")

    # Fraction of query terms the top candidate must actually contain.
    # Without this, "zzzqqxx frobnicating the wibble manifold" confidently
    # surfaced three unrelated MCP servers off a single incidental term match.
    min_coverage: float = 0.5

    max_suggestions: int = 2
    second_item_ratio: float = 0.85  # show a 2nd only if within 15% of the top
    cooldown_s: int = 900
    max_per_session: int = 3
    snooze_after_shows: int = 2
    snooze_window_days: int = 30

    # Prompt pre-filters (also enforced in the bash shim, cheaply).
    min_prompt_chars: int = 25
    min_prompt_tokens: int = 4

    # Scoring weights; must sum to 1.0.
    # Coverage is weighted close to BM25 on purpose: bm25_norm is min-max
    # scaled, so the best-of-batch always gets 1.0 even when it is a poor
    # match. Measured: playwright (coverage 1.0) ranked BELOW an app-store
    # screenshot tool (coverage 0.5) until coverage carried real weight.
    w_bm25: float = 0.38
    w_coverage: float = 0.32
    w_quality: float = 0.18
    w_trust: float = 0.12

    trust_bonus: dict = field(
        default_factory=lambda: {"green": 1.0, "yellow": 0.6, "red": 0.2}
    )

    # Cap on how many rows are ever eligible for injection. Keeps the audit
    # surface something a human can actually read.
    max_eligible: int = 2000


def load_config(**overrides) -> Config:
    cfg = Config(
        shadow=_env_flag("RDX_SHADOW"),
        disabled=_env_flag("RDX_DISABLE") or DISABLED_FLAG.exists(),
        min_score=_env_float("RDX_MIN_SCORE", float("inf")),
        min_margin=_env_float("RDX_MIN_MARGIN", float("inf")),
        min_coverage=_env_float("RDX_MIN_COVERAGE", 0.5),
    )
    if overrides:
        cfg = Config(**{**cfg.__dict__, **overrides})
    return cfg


def ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR
