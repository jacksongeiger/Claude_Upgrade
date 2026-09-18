"""Funnel registry.

Adding a source means writing one module and adding one line here.
"""

from __future__ import annotations

from typing import Any

from .base import Funnel, HttpClient, HttpError  # noqa: F401


def all_funnels() -> dict[str, Any]:
    from . import github, local_scan, marketplace, mcp_registry, npm

    funnels: dict[str, Any] = {}
    for f in marketplace.instances():
        funnels[f.name] = f
    funnels["mcp_registry"] = mcp_registry.McpRegistryFunnel()
    funnels["github"] = github.GitHubFunnel()
    funnels["npm"] = npm.NpmFunnel()
    funnels["local_scan"] = local_scan.LocalScanFunnel()
    return funnels


def get_funnel(name: str):
    funnels = all_funnels()
    if name not in funnels:
        raise KeyError(f"unknown funnel: {name} (have: {', '.join(sorted(funnels))})")
    return funnels[name]
