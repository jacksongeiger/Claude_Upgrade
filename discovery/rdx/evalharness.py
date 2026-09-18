"""The eval harness - the speed-run.

Three corpora, one command each, all offline and deterministic:

    --gate       precision/recall of the fire/stay-silent decision, measured on
                 REAL prompts mined from local transcripts
    --discovery  known-answer tests: does the right resource actually surface
    --safety     the adversarial corpus: nothing malicious may ever reach an
                 envelope

Running all three takes seconds, so a threshold change is re-testable
immediately instead of requiring a week of shadow data.

`--all` exits non-zero on any safety failure or a gate-precision regression, so
this is usable as a pre-commit gate rather than a report someone reads once.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config, db, retrieve, sanitize

# Precision is the metric that matters: a false positive on a trivial prompt is
# what makes someone disable the system, after which recall is zero forever.
MIN_PRECISION = 0.80


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


@dataclass
class EvalResult:
    name: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def total(self) -> int:
        return self.passed + self.failed + self.skipped


# --------------------------------------------------------------------------
# Safety
# --------------------------------------------------------------------------

def run_safety(corpora_dir: Path | None = None) -> EvalResult:
    corpora_dir = corpora_dir or config.CORPORA_DIR
    data = _load_yaml(corpora_dir / "safety.yaml")
    res = EvalResult("safety")

    for case in data.get("cases", []):
        cid = case.get("id", "?")
        text = case.get("text", "")
        want_quarantine = case.get("expect") == "quarantine"
        out = sanitize.sanitize_draft("tool", text)

        if out.quarantine != want_quarantine:
            res.failed += 1
            res.failures.append(
                f"{cid}: expected quarantine={want_quarantine}, "
                f"got {out.quarantine} (flags={out.flags})")
            continue

        for family in case.get("flags", []) or []:
            if family not in out.blocking_flags:
                res.failed += 1
                res.failures.append(f"{cid}: blocking family {family!r} did not fire")
                break
        else:
            for family in case.get("advisory", []) or []:
                if family not in out.flags:
                    res.failed += 1
                    res.failures.append(
                        f"{cid}: advisory family {family!r} did not fire")
                    break
            else:
                if not sanitize.envelope_safe(out.summary):
                    res.failed += 1
                    res.failures.append(f"{cid}: output violates envelope invariant")
                else:
                    res.passed += 1

    return res


def run_poison_test(conn: sqlite3.Connection) -> EvalResult:
    """End-to-end: push malicious rows straight into the DB and prove none of
    them can reach a rendered envelope."""
    from . import ingest
    from .models import ResourceDraft

    res = EvalResult("poison")
    data = _load_yaml(config.CORPORA_DIR / "safety.yaml")
    malicious = [c for c in data.get("cases", []) if c.get("expect") == "quarantine"]

    now = ingest.utcnow()
    for i, case in enumerate(malicious):
        ingest.sanitize_and_store(conn, ResourceDraft(
            id=f"mcp:poison:{i}", type="mcp", name="totally normal tool",
            slug=f"poison-{i}", funnel="poison",
            source_ref="https://example.test/x",
            summary=case["text"], url="https://example.test/x",
        ), now=now)
    conn.commit()
    db.recompute_eligibility(conn, 5000)
    conn.commit()

    cfg = config.load_config(min_score=0.0, min_margin=0.0, shadow=False)
    leaked = 0
    for probe in ("is there a tool for this", "any tool to install",
                  "how do i set up a normal tool", "any mcp server for tools"):
        decision = retrieve.evaluate(probe, conn, cfg=cfg)
        for item in decision.items:
            if item.resource.id.startswith("mcp:poison:"):
                leaked += 1
                res.failures.append(
                    f"LEAK: {item.resource.id} reached an envelope for {probe!r}")

    if leaked:
        res.failed = leaked
    else:
        res.passed = 1
        res.notes.append(
            f"{len(malicious)} malicious rows stored; none reachable")
    return res


# --------------------------------------------------------------------------
# Gate
# --------------------------------------------------------------------------

def run_gate(conn: sqlite3.Connection, *, cfg: config.Config | None = None,
             corpora_dir: Path | None = None) -> EvalResult:
    corpora_dir = corpora_dir or config.CORPORA_DIR
    res = EvalResult("gate")

    # Prefer the user's mined corpus; fall back to the shipped starter one.
    # Without the fallback `rdx eval --all` reported "No labelled prompts" on
    # every fresh install and the shipped thresholds -- the numbers every new
    # user actually runs -- had no regression test at all. gate.yaml is
    # gitignored by design (it contains real prompts), so CI would never have
    # caught a calibration regression either.
    source = corpora_dir / "gate.yaml"
    if not source.exists():
        source = corpora_dir / "gate.starter.yaml"
    data = _load_yaml(source)
    res.notes.append(f"corpus: {source.name}")

    cases = [c for c in data.get("cases", []) if c.get("label") in ("inject", "silent")]
    if not cases:
        res.notes.append(
            "No labelled prompts. Run `rdx mine` to build the corpus from your "
            "own transcripts, then label them.")
        return res

    # Shadow is forced off: the harness measures what the gate WOULD do.
    cfg = cfg or config.load_config()
    cfg = config.Config(**{**cfg.__dict__, "shadow": False})

    tp = fp = tn = fn = 0
    reasons: dict[str, int] = {}
    examples: list[str] = []

    for case in cases:
        text = str(case.get("text", ""))
        want = case.get("label") == "inject"
        decision = retrieve.evaluate(text, conn, cfg=cfg)
        got = decision.inject

        if decision.reason:
            reasons[decision.reason] = reasons.get(decision.reason, 0) + 1

        if got and want:
            tp += 1
        elif got and not want:
            fp += 1
            examples.append(
                f"FALSE FIRE: {text[:70]!r} -> "
                f"{decision.items[0].resource.slug if decision.items else '?'}")
        elif not got and not want:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    res.metrics = {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "true_positives": tp, "false_positives": fp,
        "true_negatives": tn, "false_negatives": fn,
        "labelled": len(cases),
    }
    res.notes = [f"suppression reasons: {dict(sorted(reasons.items()))}"]
    res.failures = examples[:10]

    if tp + fp == 0:
        res.notes.append(
            "Gate never fired. Thresholds are +inf until calibrated - set "
            "RDX_MIN_SCORE / RDX_MIN_MARGIN from the histogram above.")
        res.skipped = len(cases)
    elif precision < MIN_PRECISION:
        res.failed = fp
        res.passed = tp + tn
    else:
        res.passed = tp + tn
        res.skipped = fn

    return res


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

# `requires_funnel` used to be checked against a hardcoded set of funnels that
# had been BUILT. That is the wrong question: a funnel can exist and still have
# put nothing in the index -- GitHub's search API is unreachable from some
# sandboxes, so the funnel errors and contributes zero rows. The eval then ran
# github-dependent cases against an index containing no GitHub rows and
# reported them as RANKING failures, which sent debugging in exactly the wrong
# direction. Ask the index instead.
def populated_funnels(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT funnel FROM resource "
        "WHERE status = 'active' AND eligible = 1").fetchall()
    found = {str(r["funnel"]) for r in rows}
    # Corpus cases name funnel FAMILIES ("marketplace"), the index stores
    # specific ones ("mp_official", "mp_community").
    return found | {f.split("_", 1)[0] for f in found}


def run_discovery(conn: sqlite3.Connection, *, cfg: config.Config | None = None,
                  corpora_dir: Path | None = None) -> EvalResult:
    corpora_dir = corpora_dir or config.CORPORA_DIR
    data = _load_yaml(corpora_dir / "discovery.yaml")
    res = EvalResult("discovery")

    # Retrieval quality is measured independently of the gate, so thresholds
    # being uncalibrated does not hide a ranking regression.
    cfg = cfg or config.load_config()
    cfg = config.Config(**{**cfg.__dict__, "shadow": False,
                           "min_score": 0.0, "min_margin": 0.0})

    populated = populated_funnels(conn)
    for case in data.get("cases", []):
        cid = case.get("id", "?")
        requires = case.get("requires_funnel")
        if requires and requires not in populated:
            res.skipped += 1
            res.notes.append(
                f"SKIP {cid}: the '{requires}' funnel contributed no eligible "
                f"rows to this index (run `rdx sync --funnel {requires}` and "
                f"check its status)")
            continue

        prompt = str(case.get("prompt", ""))
        rows, _query = retrieve.search(conn, prompt, limit=25)
        scored = retrieve.score_candidates(
            rows, cfg, terms=retrieve.query_terms(prompt))
        # The coverage threshold decides SILENCE, which is the gate's job and
        # is tested in test_gate.py. Here we are asking "does the right thing
        # rank?", so it applies only to the cases asserting nothing should
        # surface at all.
        if case.get("expect_none"):
            # Silence is the GATE's job, so test the real gate rather than a
            # partial reimplementation of it: score threshold, margin and match
            # count together are what actually keep a nonsense query quiet.
            live = config.Config(**{**cfg.__dict__,
                                    "min_score": config.DEFAULT_MIN_SCORE,
                                    "min_margin": config.DEFAULT_MIN_MARGIN})
            decision = retrieve.evaluate(prompt, conn, cfg=live)
            scored = decision.items
        top_n = int(case.get("top_n", 5))
        got = [c.resource.slug.lower() for c in scored[:top_n]]

        if case.get("expect_none"):
            if got:
                res.failed += 1
                res.failures.append(f"{cid}: expected nothing, got {got[:3]}")
            else:
                res.passed += 1
            continue

        wanted = [str(s).lower() for s in case.get("expect_any", [])]
        if any(any(w in g for g in got) for w in wanted):
            res.passed += 1
        else:
            res.failed += 1
            res.failures.append(
                f"{cid}: wanted any of {wanted}, top-{top_n} was {got[:5]}")

    return res


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def format_report(results: list[EvalResult]) -> str:
    lines = []
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        head = f"[{status}] {r.name:<10} {r.passed} passed"
        if r.failed:
            head += f", {r.failed} failed"
        if r.skipped:
            head += f", {r.skipped} skipped"
        lines.append(head)
        for metric, value in r.metrics.items():
            lines.append(f"           {metric}: {value}")
        for note in r.notes:
            lines.append(f"           {note}")
        for failure in r.failures[:10]:
            lines.append(f"           - {failure}")
    return "\n".join(lines)
