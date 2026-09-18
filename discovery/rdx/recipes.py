"""Install recipes and trust tiers.

A recipe is an argv list, never a shell string. Nothing in this module ever
builds a command by string interpolation of upstream text: the executable is
drawn from a fixed allowlist and every argument is either a literal we wrote or
a sanitized slug. That is what makes "run the install" a bounded action rather
than arbitrary code execution driven by a registry description.

Tiers, from the design:

  green   auto-runs. Reversible, project-scoped, touches no global state.
  yellow  one keystroke. First-party marketplace, SHA-pinned source.
  red     type the slug to confirm. Everything else.

Two rules that never bend, both encoded here rather than left to the caller:

  * MCP installs default to `-s local`. A committed `.mcp.json` (project scope)
    loads WITHOUT a trust prompt in non-interactive sessions, which is a code
    execution path into every future session in that repo.
  * argv[0] must be in ALLOWED_BINARIES. Anything else degrades to `manual`,
    which prints instructions and executes nothing.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

from .models import Resource

ALLOWED_BINARIES = frozenset({"claude", "pip", "pip3", "python3", "npm", "uv", "uvx"})

# Characters that have no business in an argv element we are about to execute.
# Their presence means something built a command out of untrusted text.
SHELL_METACHARACTERS = set(";|&$`><\n\r\\")

REVERSIBLE_KINDS = frozenset({"pip", "npm", "enable_only"})


@dataclass(frozen=True)
class Recipe:
    kind: str
    tier: str
    argv: list[str]
    undo_argv: list[str] | None = None
    version: str | None = None
    env_keys: list[str] = field(default_factory=list)
    note: str | None = None
    scope: str = "local"

    @property
    def runnable(self) -> bool:
        return self.kind != "manual" and bool(self.argv)

    def display(self) -> str:
        return " ".join(shlex.quote(a) for a in self.argv) if self.argv else "(manual)"


class UnsafeRecipe(ValueError):
    """Raised when a recipe fails validation. Never repaired, only refused."""


def validate(argv: list[str]) -> None:
    if not argv:
        raise UnsafeRecipe("empty argv")
    if argv[0] not in ALLOWED_BINARIES:
        raise UnsafeRecipe(f"executable not allowed: {argv[0]!r}")
    for arg in argv:
        if not isinstance(arg, str):
            raise UnsafeRecipe(f"non-string argument: {arg!r}")
        bad = SHELL_METACHARACTERS & set(arg)
        if bad:
            raise UnsafeRecipe(
                f"shell metacharacter {''.join(sorted(bad))!r} in argument {arg!r}")


def assign_tier(resource: Resource, kind: str) -> str:
    """Deterministic tier assignment. Read top to bottom; first match wins."""
    # Anything the sanitizer flagged, or that needs credentials, gets a human.
    if resource.flags:
        return "red"
    if resource.status != "active":
        return "red"

    # Already installed, just disabled: enabling touches nothing new.
    if kind == "enable_only":
        return "green"

    # Reversible, project-scoped dependency installs.
    if kind in ("pip", "npm"):
        return "green" if resource.trust_tier == "green" else "yellow"

    # Everything else inherits the resource's own tier, which the funnels set
    # from provenance (SHA-pinned first-party marketplace vs the long tail).
    if kind == "manual":
        return "red"
    return resource.trust_tier


def build_recipe(resource: Resource, recipe_json: str | None,
                 *, scope: str = "local") -> Recipe:
    """Turn a stored recipe into a validated, executable Recipe.

    A recipe that fails validation is downgraded to `manual` rather than
    rejected outright: the user still gets the URL and instructions, just not a
    one-keystroke install.
    """
    data = {}
    if recipe_json:
        try:
            parsed = json.loads(recipe_json)
            if isinstance(parsed, dict):
                data = parsed
        except ValueError:
            data = {}

    kind = str(data.get("kind") or "manual")
    argv = data.get("argv")
    undo = data.get("undo_argv")

    if kind == "manual" or not isinstance(argv, list):
        return Recipe(kind="manual", tier="red", argv=[],
                      note=str(data.get("note") or "") or None,
                      scope=scope)

    argv = [str(a) for a in argv]
    argv = _apply_scope(argv, kind, scope)

    try:
        validate(argv)
        if isinstance(undo, list):
            undo = [str(a) for a in undo]
            validate(undo)
        else:
            undo = None
    except UnsafeRecipe:
        return Recipe(kind="manual", tier="red", argv=[],
                      note="recipe failed validation; install by hand",
                      scope=scope)

    return Recipe(
        kind=kind,
        tier=assign_tier(resource, kind),
        argv=argv,
        undo_argv=undo,
        version=data.get("version"),
        env_keys=[k for k in (data.get("env_keys") or []) if isinstance(k, str)],
        note=str(data.get("note") or "") or None,
        scope=scope,
    )


def _apply_scope(argv: list[str], kind: str, scope: str) -> list[str]:
    """Rewrite the scope flag for MCP installs.

    Project scope is never selected implicitly — the caller has to pass it, and
    the runner requires an extra explicit flag on top.
    """
    if not kind.startswith("claude_mcp"):
        return argv
    out = list(argv)
    if "-s" in out:
        i = out.index("-s")
        if i + 1 < len(out):
            out[i + 1] = scope
    else:
        out += ["-s", scope]
    return out


def describe(resource: Resource, recipe: Recipe) -> str:
    """The human-facing pre-install summary.

    Shown before anything runs, including for green-tier auto-installs, so the
    action is never invisible.
    """
    lines = [
        f"  {resource.name}  ({resource.type}, {recipe.tier} tier)",
        f"  {resource.summary}",
    ]
    if resource.url:
        lines.append(f"  source : {resource.url}")
    if resource.stars:
        lines.append(f"  stars  : {resource.stars:,}")
    lines.append(f"  command: {recipe.display()}")
    if recipe.scope != "local" and recipe.kind.startswith("claude_mcp"):
        lines.append(f"  scope  : {recipe.scope}  ** shared, loads without a "
                     f"trust prompt in non-interactive sessions **")
    if recipe.env_keys:
        lines.append(f"  needs  : {', '.join(recipe.env_keys)}")
    if "needs_secrets" in resource.flags:
        lines.append("  note   : requires a credential")
    if recipe.note:
        lines.append(f"  note   : {recipe.note}")
    if recipe.undo_argv:
        lines.append(f"  undo   : {' '.join(shlex.quote(a) for a in recipe.undo_argv)}")
    return "\n".join(lines)
