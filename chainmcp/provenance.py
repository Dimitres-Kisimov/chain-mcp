"""Machine-readable result provenance: who computed a number, on what data.

Every tool result — success or error — carries a ``provenance`` block built
here, so the honesty the repo promises in prose (``data_note``) is also a
typed, schema-validated fact an agent can branch on. The block answers, per
result: which server (name / version / commit) served it, which tool produced
it, which engine repo (package, local availability, commit) computed it, what
the data was (a canonical label, not free text), and whether the result is
deterministic. It is attached centrally by the ``_safe`` wrapper in
:mod:`chainmcp.tools`, so no tool can forget it, and it is REQUIRED by the
result envelope the contract layer publishes (:mod:`chainmcp.contract`).

Honesty rules
-------------
- **Error results make no data claims.** A failed call computed nothing, so
  its block carries only identity (server / tool / engine); ``data`` and
  ``deterministic`` appear on success results only.
- **Commits are best-effort facts, never guesses.** SHAs are read from the
  local checkout's ``.git`` with stdlib file reads only; anything unreadable
  or malformed yields ``null``, never a fabricated value.
- **Labels are canonical.** ``data.label`` comes from :data:`DATA_LABELS`;
  the contract layer cross-checks each tool's registry label against the
  data wording of its served description, so the machine-readable label and
  the human-readable text cannot drift apart.
- **"Deterministic" is scoped.** It means: same input, same result, while the
  source-repo checkout is unchanged. ``portfolio_status`` audits live repo
  state and is therefore declared non-deterministic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chainmcp import __version__
from chainmcp.config import REPOS, repo_path

SCHEMA_VERSION = 1

_ROOT = Path(__file__).resolve().parents[1]

_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")  # SHA-1 or SHA-256 object names

#: Canonical data labels a result may carry. ``caller-provided`` is set at
#: call time (e.g. ``pack_cartons`` with an item list); the rest are per-tool
#: registry defaults.
DATA_LABELS = ("synthetic", "real+derived", "real-local", "caller-provided")

#: Per-tool provenance facts: which engine repo computes the result, what the
#: default data is (canonical label + honest source string), and whether the
#: result is deterministic. Engine package names come from
#: :data:`chainmcp.config.REPOS` (single source of truth). The contract layer
#: asserts this registry covers exactly the served tool catalog.
TOOL_PROVENANCE: dict[str, dict[str, Any]] = {
    "forecast_demand": {
        "engine_repo": "decision-chain",
        "data": {
            "label": "real+derived",
            "source": (
                "UCI Online Retail II via decision-chain's provenance-tagged pipeline"
            ),
            "fields": {"history": "real", "forecasts": "derived"},
        },
        "deterministic": True,
    },
    "optimize_slotting": {
        "engine_repo": "logistics-digital-twin",
        "data": {
            "label": "synthetic",
            "source": "seeded warehouse dataset (logistics-digital-twin, seed 42)",
        },
        "deterministic": True,
    },
    "pack_cartons": {
        "engine_repo": "logistics-digital-twin",
        "data": {
            "label": "synthetic",
            "source": "seeded 60-carton dataset (logistics-digital-twin, seed 42)",
        },
        "deterministic": True,
    },
    "route_deliveries": {
        "engine_repo": "route-optimizer",
        "data": {
            "label": "synthetic",
            "source": "seeded CVRP instances (route-optimizer, committed JSON)",
        },
        "deterministic": True,
    },
    "analyze_discount_leakage": {
        "engine_repo": "sales-kpi-analytics",
        "data": {
            "label": "synthetic",
            "source": (
                "deterministically generated 24-month B2B order dataset (sales-kpi-analytics)"
            ),
        },
        "deterministic": True,
    },
    "portfolio_status": {
        "engine_repo": "portfolio-ops",
        "data": {
            "label": "real-local",
            "source": "live state of the audited local repository at call time",
        },
        "deterministic": False,  # audits live repo state; results change as repos change
    },
}

#: JSON Schema (Draft 2020-12) for the provenance block. Embedded into
#: ``chainmcp.contract.RESULT_ENVELOPE_SCHEMA`` so every envelope validation
#: in the test suite also enforces provenance.
_SHA_OR_NULL: dict[str, Any] = {
    "anyOf": [
        {"type": "string", "pattern": "^[0-9a-f]{40,64}$"},
        {"type": "null"},
    ]
}

PROVENANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "server": {
            "type": "object",
            "properties": {
                "name": {"const": "chain-mcp"},
                "version": {"type": "string", "minLength": 1},
                "commit": _SHA_OR_NULL,
            },
            "required": ["name", "version", "commit"],
        },
        "tool": {"type": "string", "minLength": 1},
        "engine": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "minLength": 1},
                "package": {"type": "string", "minLength": 1},
                "available": {"type": "boolean"},
                "commit": _SHA_OR_NULL,
            },
            "required": ["repo", "package", "available", "commit"],
        },
        "data": {
            "type": "object",
            "properties": {
                "label": {"enum": list(DATA_LABELS)},
                "source": {"type": "string", "minLength": 1},
                "fields": {
                    "type": "object",
                    "additionalProperties": {"enum": ["real", "derived", "synthetic"]},
                },
            },
            "required": ["label", "source"],
        },
        "deterministic": {"type": "boolean"},
    },
    "required": ["schema_version", "server", "tool", "engine"],
}


def git_head(repo: Path) -> str | None:
    """Best-effort commit SHA of a local checkout, via stdlib file reads only.

    Handles a normal ``.git`` directory (symbolic-ref HEAD, loose or packed
    refs), a detached HEAD, and a ``.git`` pointer file (worktrees). Returns
    ``None`` — never a guess, never an exception — when anything is missing
    or malformed. No subprocesses, no git binary required.
    """
    try:
        git_dir = repo / ".git"
        if git_dir.is_file():  # worktree / submodule pointer: "gitdir: <path>"
            text = git_dir.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None
            target = Path(text.split(":", 1)[1].strip())
            git_dir = target if target.is_absolute() else (repo / target).resolve()
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return None
        head = head_file.read_text(encoding="utf-8").strip()
        if _SHA_RE.match(head):
            return head  # detached HEAD
        if not head.startswith("ref: "):
            return None
        ref = head[len("ref: "):].strip()
        ref_file = git_dir / ref
        if ref_file.is_file():
            sha = ref_file.read_text(encoding="utf-8").strip()
            return sha if _SHA_RE.match(sha) else None
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and _SHA_RE.match(parts[0]):
                    return parts[0]
        return None
    except OSError:
        return None


def build_provenance(
    tool: str, *, override: dict[str, Any] | None = None, error: bool = False
) -> dict[str, Any]:
    """Build the provenance block for one result.

    ``override`` lets a tool refine its DATA facts at call time (only the
    ``data`` key is accepted — identity facts are not overridable), e.g.
    ``pack_cartons`` switching to ``caller-provided`` when given an item list.
    With ``error=True`` the block carries identity only — no data claims,
    because nothing was computed. Raises ``KeyError`` for a tool missing from
    :data:`TOOL_PROVENANCE` (a wiring bug the contract tests catch).
    """
    spec = TOOL_PROVENANCE[tool]
    repo_name = spec["engine_repo"]
    _env_var, package = REPOS[repo_name]
    path = repo_path(repo_name)
    available = (path / package).is_dir()

    block: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "server": {
            "name": "chain-mcp",
            "version": __version__,
            "commit": git_head(_ROOT),
        },
        "tool": tool,
        "engine": {
            "repo": repo_name,
            "package": package,
            "available": available,
            "commit": git_head(path) if available else None,
        },
    }
    if not error:
        data = dict(spec["data"])
        if override is not None:
            unknown = set(override) - {"data"}
            if unknown:
                raise ValueError(
                    f"provenance override may only refine 'data', got {sorted(unknown)}"
                )
            data.update(override.get("data", {}))
        block["data"] = data
        block["deterministic"] = spec["deterministic"]
    return block
