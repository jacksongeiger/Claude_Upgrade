"""Local scan: what is already installed on this machine.

This funnel does two jobs, and the second is the more valuable one day to day:

  1. It records installed plugins and MCP servers so they can be EXCLUDED from
     suggestions. Recommending something the user already has is the fastest
     way to make the system feel stupid.

  2. It captures tool_prefix ("mcp__github__") so PostToolUse events can later
     be attributed back to a resource, and a tool-name snapshot so drift in an
     MCP server's advertised tools is detectable.

Everything found here is trust_tier green: the user already runs it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ..models import FunnelPage, RawRecord, ResourceDraft
from .base import HttpClient

FIELD_WHITELIST = frozenset({"name", "description", "kind", "scope", "source"})


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class LocalScanFunnel:
    name = "local_scan"
    supports_delta = False
    default_trust_tier = "green"
    field_whitelist = FIELD_WHITELIST

    def __init__(self, home: Path | None = None, cwd: Path | None = None) -> None:
        self.home = home or Path.home()
        self.cwd = cwd or Path.cwd()

    # -- discovery sources -------------------------------------------------

    def _settings_plugins(self) -> list[tuple[str, str]]:
        """(plugin_name, marketplace) from ~/.claude/settings.json."""
        settings = _read_json(self.home / ".claude" / "settings.json")
        enabled = settings.get("enabledPlugins") or {}
        out: list[tuple[str, str]] = []
        if isinstance(enabled, dict):
            for key, value in enabled.items():
                if value is False:
                    continue
                name, _, marketplace = str(key).partition("@")
                out.append((name, marketplace or "unknown"))
        elif isinstance(enabled, list):
            for key in enabled:
                name, _, marketplace = str(key).partition("@")
                out.append((name, marketplace or "unknown"))
        return out

    def _mcp_servers(self) -> list[tuple[str, str, dict]]:
        """(server_name, scope, config) from user and project MCP config."""
        out: list[tuple[str, str, dict]] = []

        root = _read_json(self.home / ".claude.json")
        for name, cfg in (root.get("mcpServers") or {}).items():
            if isinstance(cfg, dict):
                out.append((str(name), "user", cfg))

        for proj_path, proj in (root.get("projects") or {}).items():
            if not isinstance(proj, dict):
                continue
            for name, cfg in (proj.get("mcpServers") or {}).items():
                if isinstance(cfg, dict):
                    out.append((str(name), "local", cfg))

        project_mcp = _read_json(self.cwd / ".mcp.json")
        for name, cfg in (project_mcp.get("mcpServers") or {}).items():
            if isinstance(cfg, dict):
                out.append((str(name), "project", cfg))

        return out

    # -- funnel protocol ---------------------------------------------------

    def fetch(self, state: dict[str, Any], http: HttpClient,
              limit: int | None = None) -> Iterator[FunnelPage]:
        records: list[RawRecord] = []

        for name, marketplace in self._settings_plugins():
            records.append(RawRecord(
                natural_key=f"plugin:{name}",
                type="plugin",
                fields={"name": name, "kind": "plugin", "scope": "user",
                        "source": marketplace,
                        "description": f"Installed plugin from {marketplace}."},
                source_ref=str(self.home / ".claude" / "settings.json"),
            ))

        for name, scope, cfg in self._mcp_servers():
            desc = f"Installed MCP server ({scope} scope)."
            cmd = cfg.get("command") or cfg.get("url")
            if isinstance(cmd, str) and cmd:
                desc = f"Installed MCP server ({scope} scope), runs {cmd}."
            records.append(RawRecord(
                natural_key=f"mcp:{name}",
                type="mcp",
                fields={"name": name, "kind": "mcp", "scope": scope,
                        "description": desc},
                source_ref="local",
            ))

        if limit is not None:
            records = records[:limit]
        yield FunnelPage(records=records)

    def to_draft(self, rec: RawRecord) -> ResourceDraft | None:
        name = str(rec.fields.get("name") or "").strip()
        if not name:
            return None
        kind = rec.fields.get("kind", "plugin")
        return ResourceDraft(
            id=f"{kind}:{self.name}:{name}",
            type="mcp" if kind == "mcp" else "plugin",
            name=name,
            slug=name,
            funnel=self.name,
            source_ref=rec.source_ref,
            summary=str(rec.fields.get("description") or ""),
            url=None,
            tags=["installed"],
            recipe_kind="enable_only",
            trust_tier="green",
        )


def record_installed_from_scan(conn, *, home: Path | None = None,
                               cwd: Path | None = None, now: str) -> int:
    """Populate installed_resource so these are excluded from suggestions.

    Kept separate from the funnel so it can run without a full ingest pass.
    """
    from .. import db

    scanner = LocalScanFunnel(home=home, cwd=cwd)
    n = 0
    for name, marketplace in scanner._settings_plugins():
        db.record_installed(
            conn, f"plugin:local_scan:{name}", scope="user", installed_at=now,
            installed_by="preexisting", version=None, tool_prefix=None)
        n += 1
    for name, scope, cfg in scanner._mcp_servers():
        db.record_installed(
            conn, f"mcp:local_scan:{name}", scope=scope, installed_at=now,
            installed_by="preexisting",
            version=None, tool_prefix=f"mcp__{name}__")
        n += 1
    return n
