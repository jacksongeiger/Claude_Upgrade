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

# The live switch, as a FILE rather than an environment variable.
#
# The installer used to say "flip RDX_SHADOW=0 in your shell profile to go
# live", which is unreliable on the platform this targets: a macOS app
# launched from Spotlight or the Dock does not inherit ~/.zshrc, so the
# variable is simply absent and the system stays silent forever with no
# indication why. A file in the state directory is read identically however
# Claude Code was started. `rdx on` / `rdx off` manage it.
LIVE_FLAG = STATE_DIR / "LIVE"

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


# Provisional thresholds, derived from a 68-case labelled corpus
# (corpora/gate.starter.yaml) swept against a real 3,980-resource index:
#
#   score  margin  precision  recall
#   0.55   0.00    0.82       0.90
#   0.60   0.00    1.00       0.70   <- shipped
#   0.65   0.00    1.00       0.50
#
# 0.60 is the knee: zero false fires across 48 ordinary-work prompts while
# still catching 14 of 20 genuine acquisition prompts. `rdx mine` + `rdx eval
# --gate` against real transcript history should replace these.
DEFAULT_MIN_SCORE = 0.60
DEFAULT_MIN_MARGIN = 0.0


@dataclass(frozen=True)
class Config:
    """Runtime knobs.

    Shadow mode is ON by default: a fresh install evaluates and logs every
    prompt but injects nothing until someone explicitly sets RDX_SHADOW=0.
    The installer promises this; the default enforces it.
    """

    db_path: Path = DB_PATH
    shadow: bool = True
    disabled: bool = False

    min_score: float = DEFAULT_MIN_SCORE
    min_margin: float = DEFAULT_MIN_MARGIN

    # How many content terms the top candidate must actually contain. This is
    # the defense against "zzzqqxx frobnicating the wibble manifold", which
    # matches ZERO terms; a real query matches at least one. Testing a FRACTION
    # here instead silently dropped 7 of 20 genuine acquisition prompts purely
    # for being wordy, and left min_score doing nothing.
    min_matched_terms: int = 1

    # Task-shaped prompts did not ask for anything, so an unsolicited
    # suggestion has to be better evidenced: at least TWO content terms
    # actually present in the resource, not one.
    #
    # 0.55, swept against all 123 cases in corpora/gate.starter.yaml:
    #
    #     thr    precision  recall   TP  FP  FN
    #     0.60     0.938     0.536   15   1  13
    #     0.55     0.900     0.643   18   2  10     <- knee
    #     0.50     0.857     0.643   18   3  10
    #     0.45 and below: identical to 0.50
    #
    # 0.55 is where the curve turns: three more true positives for one more
    # false positive, and nothing below it buys any recall at all -- the
    # remaining misses fail on term coverage, not score, so lowering further
    # only costs precision.
    #
    # This was 0.60 until the in-codebase suppressor (CODEBASE_RE) landed. That
    # check removed 7 of 8 false fires on its own, which is what created the
    # precision headroom to spend here. Ordering matters: swept before the
    # suppressor existed, 0.55 looked reckless.
    min_score_task: float = 0.55
    min_matched_terms_task: int = 2

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
    # Shadow defaults ON, and only an explicit action turns it off, so nothing
    # forgotten can silently go live. Precedence, most specific first:
    #
    #   1. RDX_SHADOW, when set  -- per-process override; the eval harness
    #                               relies on this to force live for one run
    #   2. the LIVE flag file    -- the durable, GUI-safe user setting
    #   3. shadow                -- the default
    raw_shadow = os.environ.get("RDX_SHADOW")
    if raw_shadow is not None:
        shadow = raw_shadow.strip().lower() not in {"0", "false", "no", "off", ""}
    else:
        shadow = not LIVE_FLAG.exists()

    cfg = Config(
        shadow=shadow,
        disabled=_env_flag("RDX_DISABLE") or DISABLED_FLAG.exists(),
        min_score=_env_float("RDX_MIN_SCORE", DEFAULT_MIN_SCORE),
        min_margin=_env_float("RDX_MIN_MARGIN", DEFAULT_MIN_MARGIN),
        min_matched_terms=int(_env_float("RDX_MIN_MATCHED_TERMS", 1)),
        min_score_task=_env_float("RDX_MIN_SCORE_TASK", 0.55),
    )
    if overrides:
        cfg = Config(**{**cfg.__dict__, **overrides})
    return cfg


def ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR
