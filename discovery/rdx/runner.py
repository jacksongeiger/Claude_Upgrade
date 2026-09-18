"""Install execution.

The only place in rdx that runs a subprocess. Everything it executes has been
through `recipes.validate()`, runs with `shell=False`, and is drawn from a
fixed binary allowlist.

Structural guarantee worth stating plainly: **the hook can never reach this
module.** `hook.py` imports only config, db and retrieve; its single output is
text. Installation happens because a human typed `rdx install`, never because a
suggestion was rendered. That separation is the reason a malicious registry
description cannot cause an install — the worst it can do is appear in a list.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from . import db, ingest
from .models import Resource
from .recipes import Recipe, build_recipe, describe

TIMEOUT_S = 300


@dataclass
class RunResult:
    ok: bool
    ran: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _confirm_exact(expected: str) -> bool:
    try:
        return input(f"  type '{expected}' to confirm: ").strip() == expected
    except (EOFError, KeyboardInterrupt):
        return False


def execute(resource: Resource, recipe: Recipe, *, conn=None,
            dry_run: bool = False, assume_yes: bool = False,
            allow_project_scope: bool = False,
            interactive: bool = True) -> RunResult:
    """Run one install recipe, gated by its tier.

    green   runs without a prompt, but always prints what it did
    yellow  one y/N keystroke
    red     must retype the slug
    """
    print(describe(resource, recipe))
    print()

    if not recipe.runnable:
        return RunResult(ok=True, ran=False,
                         message="manual install — see the source URL above")

    # Project scope is a code-execution path into every future session in the
    # repo, so it needs an explicit flag on top of whatever the tier asks for.
    if recipe.scope == "project" and not allow_project_scope:
        return RunResult(
            ok=False, ran=False,
            message="project scope requires --i-understand-project-scope: a "
                    "committed .mcp.json loads without a trust prompt in "
                    "non-interactive sessions")

    if dry_run:
        return RunResult(ok=True, ran=False,
                         message=f"dry run — would execute: {recipe.display()}")

    if not _authorized(recipe, resource, assume_yes=assume_yes,
                       interactive=interactive):
        return RunResult(ok=False, ran=False, message="cancelled")

    try:
        proc = subprocess.run(recipe.argv, shell=False, capture_output=True,
                              text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return RunResult(ok=False, ran=False,
                         message=f"{recipe.argv[0]} not found on PATH")
    except subprocess.TimeoutExpired:
        return RunResult(ok=False, ran=True,
                         message=f"timed out after {TIMEOUT_S}s")

    ok = proc.returncode == 0
    if ok and conn is not None:
        db.record_installed(
            conn, resource.id, scope=recipe.scope, installed_at=ingest.utcnow(),
            installed_by="rdx", version=recipe.version,
            tool_prefix=f"mcp__{resource.slug}__" if resource.type == "mcp" else None,
        )

    return RunResult(ok=ok, ran=True, returncode=proc.returncode,
                     stdout=proc.stdout[-4000:], stderr=proc.stderr[-4000:],
                     message="installed" if ok else "install failed")


def _authorized(recipe: Recipe, resource: Resource, *, assume_yes: bool,
                interactive: bool) -> bool:
    if recipe.tier == "green":
        # Auto-runs, but never silently: describe() has already printed the
        # command and the undo line above.
        print("  [green] reversible — running automatically")
        return True

    if recipe.tier == "red":
        # No -y bypass. Red exists precisely for the cases where a reflexive
        # "yes" is the failure mode.
        if not interactive:
            print("  [red] refused: needs an interactive confirmation")
            return False
        print(f"  [red] needs explicit confirmation ({resource.slug})")
        return _confirm_exact(resource.slug)

    if assume_yes:
        print("  [yellow] auto-confirmed via -y")
        return True
    if not interactive:
        print("  [yellow] refused: no tty and -y not given")
        return False
    return _confirm("  [yellow] install? [y/N] ")


def install(conn, slug: str, *, dry_run: bool = False, assume_yes: bool = False,
            scope: str = "local", allow_project_scope: bool = False,
            interactive: bool = True) -> RunResult:
    """Look up a slug and install it."""
    row = conn.execute(
        "SELECT * FROM resource WHERE slug = ? "
        "ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END LIMIT 1",
        (slug,)).fetchone()
    if row is None:
        return RunResult(ok=False, ran=False, message=f"no resource {slug!r}")

    resource = db._row_to_resource(row)

    if resource.status == "quarantined":
        return RunResult(
            ok=False, ran=False,
            message=f"{slug!r} is quarantined by the sanitizer "
                    f"({', '.join(resource.flags)}) — refusing to install")
    if resource.status == "deprecated":
        print(f"  WARNING: {slug!r} is marked deprecated (archived or removed "
              f"upstream)")

    already = conn.execute(
        "SELECT 1 FROM installed_resource WHERE resource_id = ? "
        "AND removed_at IS NULL", (resource.id,)).fetchone()
    if already:
        return RunResult(ok=True, ran=False, message=f"{slug!r} already installed")

    recipe = build_recipe(resource, row["recipe_json"], scope=scope)
    return execute(resource, recipe, conn=conn, dry_run=dry_run,
                   assume_yes=assume_yes,
                   allow_project_scope=allow_project_scope,
                   interactive=interactive)
