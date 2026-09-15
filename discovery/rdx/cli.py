"""rdx command line.

    rdx init                  create the index
    rdx sync [--funnel F]     fetch and store (--limit N for a bounded dry run)
    rdx scan                  record locally installed plugins / MCP servers
    rdx search "<prompt>"     show what the gate WOULD do, with the envelope
    rdx audit [--quarantined] browse the index and the quarantine queue
    rdx mine                  build corpora/gate.yaml from your transcripts
    rdx eval [--gate|...]     run the eval harness
    rdx stats                 injections, suppression histogram, accept rate
    rdx statusline            render the statusline (used by settings.json)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (config, db, evalharness, ingest, measure, mine, retrieve,
               runner, statusline)


def _open(readonly: bool = False):
    if not config.DB_PATH.exists():
        print(f"no index at {config.DB_PATH}; run `rdx init` first",
              file=sys.stderr)
        raise SystemExit(2)
    return db.open_db(config.DB_PATH, readonly=readonly)


# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    config.ensure_state_dir()
    conn = db.init_db(config.DB_PATH)
    print(f"initialized {config.DB_PATH} (schema v{db.schema_version(conn)})")
    print("FTS5: available")
    return 0


def cmd_sync(args) -> int:
    conn = _open()
    names = [args.funnel] if args.funnel else None
    reports = ingest.sync_all(conn, full=not args.limit, limit=args.limit,
                              only=names)
    for r in reports:
        status = "304 not modified" if r.not_modified else r.status
        line = (f"{r.funnel:<16} {status:<8} seen={r.n_seen:<6} "
                f"stored={r.n_upserted:<6} dropped={r.n_dropped:<5} "
                f"quarantined={r.n_quarantined}")
        if r.n_deprecated:
            line += f" deprecated={r.n_deprecated}"
        if r.error:
            line += f"  ERROR: {r.error}"
        print(line)
    print()
    print("index:", db.counts(conn))
    return 0


def cmd_scan(args) -> int:
    from .funnels.local_scan import record_installed_from_scan

    conn = _open()
    n = record_installed_from_scan(conn, now=ingest.utcnow())
    print(f"recorded {n} already-installed resources (excluded from suggestions)")
    for row in conn.execute(
            "SELECT resource_id, scope, tool_prefix FROM installed_resource "
            "WHERE removed_at IS NULL ORDER BY resource_id"):
        prefix = row["tool_prefix"] or "-"
        print(f"  {row['resource_id']:<44} {row['scope']:<8} {prefix}")
    return 0


def cmd_search(args) -> int:
    conn = _open(readonly=True)
    prompt = " ".join(args.prompt)
    cfg = config.load_config()
    if args.live:
        cfg = config.Config(**{**cfg.__dict__, "shadow": False})

    rows, query = retrieve.search(conn, prompt, limit=25)
    scored = retrieve.score_candidates(rows, cfg,
                                       terms=retrieve.query_terms(prompt))

    print(f"query      : {query or '(none)'}")
    print(f"content    : {retrieve.coverage_terms(retrieve.query_terms(prompt))}")
    print(f"candidates : {len(scored)}")
    print()
    for c in scored[:10]:
        r = c.resource
        print(f"  {c.score:.3f}  cov={c.coverage:.2f}  {r.trust_tier:<6} "
              f"{r.slug:<30} {r.summary[:56]}")

    decision = retrieve.evaluate(prompt, conn, cfg=cfg)
    print()
    print(f"decision   : {'INJECT' if decision.inject else 'silent'}"
          f"  reason={decision.reason or '-'}"
          f"  top={decision.top_score}  margin={decision.margin}"
          f"  {decision.latency_ms}ms")

    if decision.items:
        print()
        print("envelope that would be injected:")
        print()
        for line in retrieve.render_envelope(decision.items, 0).splitlines():
            print(f"    {line}")
    return 0


def cmd_audit(args) -> int:
    conn = _open(readonly=True)
    if args.quarantined:
        rows = conn.execute(
            "SELECT slug, funnel, blocking_flags, summary FROM resource "
            "WHERE status = 'quarantined' ORDER BY funnel, slug").fetchall()
        print(f"{len(rows)} quarantined resource(s)\n")
        for r in rows:
            print(f"  {r['slug']}  [{r['funnel']}]")
            print(f"    flags  : {r['blocking_flags']}")
            print(f"    summary: {r['summary'][:110]}")
            print()
        return 0

    counts = db.counts(conn)
    print("index:", counts)
    print()
    rows = conn.execute(
        "SELECT slug, type, trust_tier, funnel, quality_score, summary "
        "FROM resource WHERE eligible = 1 "
        "ORDER BY quality_score DESC, slug LIMIT ?", (args.limit,)).fetchall()
    for r in rows:
        print(f"  {r['quality_score']:.3f} {r['trust_tier']:<6} {r['type']:<7} "
              f"{r['slug']:<30} {r['summary'][:52]}")
    if counts["eligible"] > args.limit:
        print(f"\n  ... {counts['eligible'] - args.limit} more "
              f"(use --limit to see them)")
    return 0


def cmd_install(args) -> int:
    import sys as _sys

    conn = _open()
    result = runner.install(
        conn, args.slug, dry_run=args.dry_run, assume_yes=args.yes,
        scope=args.scope, allow_project_scope=args.i_understand_project_scope,
        interactive=_sys.stdin.isatty(),
    )
    print(f"  -> {result.message}")
    if result.stderr.strip():
        print(result.stderr.strip()[-1200:])
    return 0 if result.ok else 1


def cmd_mine(args) -> int:
    prompts = mine.mine()
    if not prompts:
        print("no transcripts found under ~/.claude/projects", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else config.CORPORA_DIR / "gate.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(mine.to_yaml(prompts), encoding="utf-8")

    inject = sum(1 for p in prompts if mine.heuristic_label(p.text) == "inject")
    print(f"mined {len(prompts)} prompts -> {out}")
    print(f"  pre-labelled: {inject} inject, {len(prompts) - inject} silent")
    print()
    print("Next: open the file and correct the labels, then run `rdx eval --gate`.")
    print("This file contains your own prompts and is gitignored.")
    return 0


def cmd_eval(args) -> int:
    results = []
    run_all = args.all or not (args.gate or args.discovery or args.safety)

    if run_all or args.safety:
        results.append(evalharness.run_safety())
        results.append(evalharness.run_poison_test(db.init_db(":memory:")))
    if run_all or args.discovery:
        results.append(evalharness.run_discovery(_open(readonly=True)))
    if run_all or args.gate:
        results.append(evalharness.run_gate(_open(readonly=True)))

    print(evalharness.format_report(results))

    failed = [r for r in results if not r.ok]
    if failed:
        print()
        print(f"FAILED: {', '.join(r.name for r in failed)}")
        return 1
    return 0


def cmd_stats(args) -> int:
    conn = _open()

    drained = measure.drain_spool(conn)
    if drained.events_stored:
        print(f"drained {drained.events_stored} tool events "
              f"({drained.attributed} attributed to an installed resource)")
        print()

    total = conn.execute("SELECT COUNT(*) FROM injection").fetchone()[0]
    shown = conn.execute(
        "SELECT COUNT(*) FROM injection WHERE n_shown > 0").fetchone()[0]
    print(f"evaluations : {total}")
    print(f"injections  : {shown}")
    print()

    print("suppression reasons:")
    for row in conn.execute(
            "SELECT suppressed_reason, COUNT(*) n FROM injection "
            "WHERE suppressed_reason IS NOT NULL "
            "GROUP BY suppressed_reason ORDER BY n DESC"):
        pct = 100 * row["n"] / total if total else 0
        bar = "#" * int(pct / 2)
        print(f"  {row['suppressed_reason']:<20} {row['n']:>6} "
              f"{pct:>5.1f}%  {bar}")
    print()

    row = conn.execute(
        "SELECT AVG(latency_ms) avg, MAX(latency_ms) max FROM injection"
    ).fetchone()
    if row["avg"] is not None:
        print(f"latency     : avg {row['avg']:.1f}ms  max {row['max']}ms")

    scores = [r[0] for r in conn.execute(
        "SELECT top_score FROM injection WHERE top_score IS NOT NULL "
        "ORDER BY top_score")]
    if scores:
        def pct(p):
            return scores[min(len(scores) - 1, int(len(scores) * p))]
        print(f"top_score   : p50 {pct(0.5):.3f}  p75 {pct(0.75):.3f}  "
              f"p90 {pct(0.90):.3f}  max {scores[-1]:.3f}")
        print()
        print("  Calibration: set RDX_MIN_SCORE near p75-p90 to fire on roughly")
        print("  10-25% of prompts that already passed the intent gate.")

    stats = measure.accept_rate(conn)
    if stats["shown"]:
        print()
        print(f"accept rate : {100 * stats['rate']:.1f}%  "
              f"({stats['accepted']}/{stats['shown']}) "
              f"within {measure.ACCEPT_WINDOW_MINUTES}min")

    unaccepted = measure.top_unaccepted(conn, limit=5)
    if unaccepted:
        print()
        print("suggested but never installed:")
        for slug, n in unaccepted:
            print(f"  {slug:<30} shown {n}x")

    drift = measure.tool_drift(conn)
    if drift:
        print()
        print("TOOL DRIFT since install (an MCP server grew new tools):")
        for d in drift:
            print(f"  {d.resource_id}: +{', '.join(d.added)}")
    return 0


def cmd_statusline(args) -> int:
    return statusline.main()


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rdx", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the index").set_defaults(func=cmd_init)

    s = sub.add_parser("sync", help="fetch from funnels")
    s.add_argument("--funnel", help="only this funnel")
    s.add_argument("--limit", type=int, help="bounded dry run")
    s.set_defaults(func=cmd_sync)

    sub.add_parser("scan", help="record installed resources").set_defaults(
        func=cmd_scan)

    s = sub.add_parser("search", help="show what the gate would do")
    s.add_argument("prompt", nargs="+")
    s.add_argument("--live", action="store_true",
                   help="ignore shadow mode for this query")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("audit", help="browse the index")
    s.add_argument("--quarantined", action="store_true")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("install", help="install a resource by slug")
    s.add_argument("slug")
    s.add_argument("--dry-run", action="store_true",
                   help="print the exact argv without executing")
    s.add_argument("-y", "--yes", action="store_true",
                   help="auto-confirm yellow tier (never red)")
    s.add_argument("--scope", default="local",
                   choices=["local", "user", "project"])
    s.add_argument("--i-understand-project-scope", action="store_true",
                   help="required for --scope project: a committed .mcp.json "
                        "loads without a trust prompt in non-interactive sessions")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("mine", help="build the gate corpus from transcripts")
    s.add_argument("--out")
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("eval", help="run the eval harness")
    s.add_argument("--gate", action="store_true")
    s.add_argument("--discovery", action="store_true")
    s.add_argument("--safety", action="store_true")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_eval)

    sub.add_parser("stats", help="injection and accept-rate stats").set_defaults(
        func=cmd_stats)
    sub.add_parser("statusline", help="render the statusline").set_defaults(
        func=cmd_statusline)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
