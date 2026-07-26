"""Source-repo locations and the graceful-failure contract.

Every tool imports its engine from a sibling portfolio repository, read-only.
The repo roots resolve, in order:

1. a per-repo environment variable (``CHAINMCP_DECISION_CHAIN``, ...);
2. ``CHAINMCP_ROOT`` (default ``C:\\Users\\dimik``) joined with the repo name.

Resolution happens at call time (not import time) so a missing repo produces a
clear, structured error result from the tool instead of crashing the server —
and so tests can point a repo at a bogus path to exercise that behaviour.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_ROOT = r"C:\Users\dimik"

# repo folder name -> (env var override, package dir that must exist inside it)
REPOS: dict[str, tuple[str, str]] = {
    "decision-chain": ("CHAINMCP_DECISION_CHAIN", "chain"),
    "logistics-digital-twin": ("CHAINMCP_LOGISTICS_DIGITAL_TWIN", "logitwin"),
    "route-optimizer": ("CHAINMCP_ROUTE_OPTIMIZER", "routeopt"),
    "sales-kpi-analytics": ("CHAINMCP_SALES_KPI_ANALYTICS", "saleskpi"),
    "portfolio-ops": ("CHAINMCP_PORTFOLIO_OPS", "ops"),
}


class EngineUnavailableError(RuntimeError):
    """A source repo (or its package) is not where the config says it is."""


def portfolio_root() -> Path:
    return Path(os.environ.get("CHAINMCP_ROOT", DEFAULT_ROOT))


def repo_path(name: str) -> Path:
    """Resolved (but not validated) path of a source repo."""
    env_var, _pkg = REPOS[name]
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    return portfolio_root() / name


def ensure_repo(name: str) -> Path:
    """Resolve a source repo, validate it, and put it on ``sys.path``.

    Raises :class:`EngineUnavailableError` with a message that says exactly
    which path was tried and how to fix the configuration.
    """
    env_var, pkg = REPOS[name]
    path = repo_path(name)
    if not (path / pkg).is_dir():
        raise EngineUnavailableError(
            f"source repo '{name}' not found: expected package '{pkg}' under {path}. "
            f"Set the {env_var} environment variable (or CHAINMCP_ROOT) to the folder "
            f"that contains the '{name}' repository."
        )
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return path
