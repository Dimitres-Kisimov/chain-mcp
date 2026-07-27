"""Shared test helpers: the CI split between engine tests and self-contained tests.

The engine tools import from SIBLING portfolio repos that exist on the dev
machine but not in CI. Tests that exercise a real engine are decorated with
``requires_repos(...)`` and skip -- with an explicit reason -- when the sibling
repo is not where the config resolves it. Everything else (input validation,
missing-repo fallback, the JSON-RPC handshake/schema/stdout-purity tests) is
self-contained and runs everywhere.

To simulate CI locally: point CHAINMCP_ROOT at an empty directory and run
pytest -- the engine tests skip, the rest must stay green.
"""

from __future__ import annotations

import pytest

from chainmcp.config import REPOS, repo_path


def repo_available(name: str) -> bool:
    """True when the sibling repo's package dir resolves (same check as ensure_repo)."""
    _env_var, pkg = REPOS[name]
    return (repo_path(name) / pkg).is_dir()


def requires_repos(*names: str) -> pytest.MarkDecorator:
    """Skip a test unless every named sibling source repo is present."""
    missing = [n for n in names if not repo_available(n)]
    return pytest.mark.skipif(
        bool(missing),
        reason=(
            "sibling source repo(s) not present: " + ", ".join(missing) +
            " -- engine tests only run where the portfolio repos are checked out "
            "(set CHAINMCP_ROOT or the per-repo env vars); protocol and "
            "validation tests still run"
        ) if missing else "",
    )
