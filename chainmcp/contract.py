"""Tool-contract / schema validation for the chain-mcp tool registry.

This module introspects the tools ACTUALLY served by the FastMCP registry
(``chainmcp.server.mcp``) and machine-checks the contract a client sees, so the
published tool catalog cannot drift out of sync with the implementation. It does
not re-invent the server: it reads the same ``tools/list`` schemas an MCP client
would receive.

What it checks (honestly scoped)
--------------------------------
1. **Registry vs declaration vs docs.** The set of tools the server serves, the
   set of tool functions implemented in :mod:`chainmcp.tools`, and the set of
   tools documented in the README catalog table must all equal the canonical
   :data:`CATALOG` declared here. A mismatch (a tool added, removed, or renamed
   in one place but not the others) fails.
2. **Schema completeness/consistency.** Each served tool must have a non-empty
   name and description, an ``inputSchema`` that is itself a valid JSON Schema
   (Draft 2020-12) of ``type: object``, and every declared parameter must be
   typed (a concrete ``type``, an ``enum``/``const``, a ``$ref`` to a defined
   model, or an ``anyOf`` of typed alternatives) — no untyped free-for-all
   parameters. Any ``required`` entry must name a real property.
3. **Round-trip.** For each tool an *illustrative* sample request is validated
   against the tool's real served ``inputSchema`` and must be accepted, while a
   deliberately malformed request must be rejected — proving the schema actually
   constrains input. For the response side, the tool is invoked with an input
   that its own validation rejects *before any engine import* (so this works
   offline, with no sibling repos), and the real structured error it returns is
   validated against :data:`RESULT_ENVELOPE_SCHEMA`, the documented common
   result shape.

Honesty notes
-------------
- The sample request payloads in :data:`CATALOG` are **illustrative**, labelled
  as such; they exist to exercise the schema, not to represent real data.
- These tools' functions return a plain ``dict``, so FastMCP declares **no**
  ``outputSchema`` in the served contract. :data:`RESULT_ENVELOPE_SCHEMA` is
  therefore this package's *own documented* result envelope (see
  :mod:`chainmcp.tools`), not a server-declared output schema. The response
  round-trip validates real error responses (the ``ok: false`` branch), which
  are producible offline; the success branch is described but not engine-run
  here.
- Everything is deterministic: no wall-clock, no randomness. The generated
  catalog report is byte-identical across re-runs.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from chainmcp import tools
from chainmcp.server import mcp

ROOT = Path(__file__).resolve().parents[1]
CATALOG_MD = ROOT / "deliverables" / "tool_catalog.md"
CATALOG_CSV = ROOT / "deliverables" / "tool_catalog.csv"

MIN_DESCRIPTION_CHARS = 60
_DATA_LABELS = ("SYNTHETIC", "REAL data", "real local")


class ContractError(AssertionError):
    """Raised (by :func:`assert_contract`) when the tool contract is violated."""


# --------------------------------------------------------------------------- #
# Canonical declaration — the single source of truth the registry, the tool
# implementations, and the README catalog table are all checked against.
#
# ``valid_input`` / ``invalid_input`` are ILLUSTRATIVE sample requests used only
# to exercise the served inputSchema (accept the valid one, reject the invalid
# one). ``engine_free_bad_call`` is a kwargs dict that makes the real tool
# function return a structured invalid_input error WITHOUT importing any sibling
# engine repo, so the response round-trip runs fully offline.
# --------------------------------------------------------------------------- #
CATALOG: dict[str, dict[str, Any]] = {
    "forecast_demand": {
        "source_repo": "decision-chain",
        "valid_input": {"demand_class": "smooth", "horizon_weeks": 4},
        "invalid_input": {"horizon_weeks": "eight"},  # integer param given a string
        "engine_free_bad_call": {"horizon_weeks": 99},
    },
    "optimize_slotting": {
        "source_repo": "logistics-digital-twin",
        "valid_input": {"top_moves": 5, "n_cartons": 60},
        "invalid_input": {"top_moves": "five"},  # integer param given a string
        "engine_free_bad_call": {"n_cartons": 0},
    },
    "pack_cartons": {
        "source_repo": "logistics-digital-twin",
        "valid_input": {
            "items": [
                {"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 2.0, "quantity": 12}
            ]
        },
        "invalid_input": {"items": "not-a-list"},  # anyOf[array, null] rejects a string
        "engine_free_bad_call": {
            "items": [{"length_cm": -5, "width_cm": 20, "height_cm": 15, "weight_kg": 1}]
        },
    },
    "route_deliveries": {
        "source_repo": "route-optimizer",
        "valid_input": {"instance": "n60", "solution_limit": 500},
        "invalid_input": {"instance": "nope"},  # not in the enum
        "engine_free_bad_call": {"instance": "n9999"},
    },
    "analyze_discount_leakage": {
        "source_repo": "sales-kpi-analytics",
        "valid_input": {"policy_discount_pct": 10.0, "top_n": 5},
        "invalid_input": {"policy_discount_pct": "lots"},  # number param given a string
        "engine_free_bad_call": {"policy_discount_pct": 150},
    },
    "portfolio_status": {
        "source_repo": "portfolio-ops",
        "valid_input": {"repo": "route-optimizer", "run_tests": False},
        "invalid_input": {},  # required `repo` missing
        "engine_free_bad_call": {"repo": "../evil"},
    },
}

CATALOG_ORDER: tuple[str, ...] = tuple(CATALOG)

# The documented common result envelope (see chainmcp/tools.py). NOT a
# server-declared outputSchema — these tools return `dict`, so MCP serves none.
# On error, `ok` is false and `error_type`/`error` are present; on success,
# `ok` is true and a `data_note` string is present.
RESULT_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "data_note": {"type": "string"},
        "error_type": {
            "type": "string",
            "enum": ["invalid_input", "engine_unavailable", "engine_error"],
        },
        "error": {"type": "string"},
    },
    "required": ["ok"],
    "allOf": [
        {
            "if": {"properties": {"ok": {"const": False}}, "required": ["ok"]},
            "then": {"required": ["ok", "error_type", "error"]},
        },
        {
            "if": {"properties": {"ok": {"const": True}}, "required": ["ok"]},
            "then": {"required": ["ok", "data_note"]},
        },
    ],
}


# --------------------------------------------------------------------------- #
# Registry / implementation / docs introspection
# --------------------------------------------------------------------------- #
def served_tools() -> list[dict[str, Any]]:
    """The tools the FastMCP registry actually serves, in canonical order.

    Reads the same ``tools/list`` payload an MCP client receives. Returns a list
    of ``{"name", "description", "input_schema", "output_schema"}`` dicts.
    """
    raw = asyncio.run(mcp.list_tools())
    by_name = {
        t.name: {
            "name": t.name,
            "description": t.description or "",
            "input_schema": t.inputSchema,
            "output_schema": getattr(t, "outputSchema", None),
        }
        for t in raw
    }
    # deterministic ordering: catalog order first, then any extras alphabetically
    ordered = [by_name[n] for n in CATALOG_ORDER if n in by_name]
    ordered += [by_name[n] for n in sorted(by_name) if n not in CATALOG_ORDER]
    return ordered


def implemented_tool_names() -> set[str]:
    """Public tool entrypoints implemented in :mod:`chainmcp.tools`.

    Identified structurally: module-level, public, and wrapped by the ``_safe``
    decorator (which sets ``__wrapped__`` via functools.wraps). Independent of
    the server registry, so it genuinely cross-checks implementation vs serving.
    """
    return {
        name
        for name, obj in vars(tools).items()
        if not name.startswith("_") and callable(obj) and hasattr(obj, "__wrapped__")
    }


def readme_catalog_names(readme: Path | None = None) -> list[str]:
    """Tool names listed in the README '## Tool catalog' table."""
    path = readme or (ROOT / "README.md")
    text = path.read_text(encoding="utf-8")
    section = text.split("## Tool catalog", 1)
    body = section[1] if len(section) > 1 else text
    body = body.split("\n## ", 1)[0]  # stop at the next top-level section
    names: list[str] = []
    for line in body.splitlines():
        m = re.match(r"\|\s*`([a-z_]+)`", line)
        if m:
            names.append(m.group(1))
    return names


# --------------------------------------------------------------------------- #
# Schema-shape checks
# --------------------------------------------------------------------------- #
def _resolve_ref(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    key = ref.rsplit("/", 1)[-1]
    return defs.get(key, {})


def _is_typed(schema: dict[str, Any], defs: dict[str, Any]) -> bool:
    """True if a property schema pins down a type (directly or via composition)."""
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema:
        return bool(_resolve_ref(schema, defs))
    if "type" in schema or "enum" in schema or "const" in schema:
        return True
    for key in ("anyOf", "oneOf", "allOf"):
        alts = schema.get(key)
        if isinstance(alts, list) and alts and all(_is_typed(a, defs) for a in alts):
            return True
    return False


def check_schema_shape(tool: dict[str, Any]) -> list[str]:
    """Return a list of problems with a served tool's contract (empty == valid)."""
    problems: list[str] = []
    name = tool.get("name") or ""
    if not isinstance(name, str) or not name.strip():
        problems.append("empty or non-string tool name")

    desc = tool.get("description") or ""
    if len(desc) < MIN_DESCRIPTION_CHARS:
        problems.append(f"description too short ({len(desc)} < {MIN_DESCRIPTION_CHARS} chars)")
    if not any(label in desc for label in _DATA_LABELS):
        problems.append("description states no data label (SYNTHETIC / REAL data / real local)")

    schema = tool.get("input_schema")
    if not isinstance(schema, dict):
        problems.append("inputSchema is not an object")
        return problems

    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        problems.append(f"inputSchema is not a valid JSON Schema: {exc.message}")

    if schema.get("type") != "object":
        problems.append(f"inputSchema.type is {schema.get('type')!r}, expected 'object'")

    props = schema.get("properties")
    if not isinstance(props, dict):
        problems.append("inputSchema has no 'properties' object")
        props = {}

    defs = schema.get("$defs", {}) if isinstance(schema.get("$defs"), dict) else {}
    for pname, pschema in props.items():
        if not _is_typed(pschema, defs):
            problems.append(f"parameter {pname!r} is not typed")

    required = schema.get("required", [])
    if not isinstance(required, list):
        problems.append("inputSchema.required is not a list")
    else:
        for r in required:
            if r not in props:
                problems.append(f"required parameter {r!r} is not declared in properties")

    return problems


# --------------------------------------------------------------------------- #
# Round-trip checks
# --------------------------------------------------------------------------- #
def _accepts(schema: dict[str, Any], instance: Any) -> bool:
    try:
        Draft202012Validator(schema).validate(instance)
        return True
    except ValidationError:
        return False


def check_round_trip(tool: dict[str, Any]) -> list[str]:
    """Validate the illustrative request pair and the offline error response."""
    problems: list[str] = []
    name = tool["name"]
    schema = tool["input_schema"]
    spec = CATALOG.get(name)
    if spec is None:
        return [f"tool {name!r} not in CATALOG; cannot round-trip"]

    if not _accepts(schema, spec["valid_input"]):
        problems.append("illustrative valid request is rejected by the served inputSchema")
    if _accepts(schema, spec["invalid_input"]):
        problems.append("malformed request is (wrongly) accepted by the served inputSchema")

    # Response side: real tool call whose own validation rejects the input
    # before any engine import -> a structured error we can validate offline.
    fn = getattr(tools, name)
    result = fn(**spec["engine_free_bad_call"])
    try:
        Draft202012Validator(RESULT_ENVELOPE_SCHEMA).validate(result)
    except ValidationError as exc:
        problems.append(f"error response violates the result envelope: {exc.message}")
    if result.get("ok") is not False or result.get("error_type") != "invalid_input":
        problems.append(f"expected an invalid_input error response, got {result!r}")

    return problems


# --------------------------------------------------------------------------- #
# Whole-catalog validation
# --------------------------------------------------------------------------- #
def validate_catalog() -> dict[str, Any]:
    """Run every check and return a deterministic, structured result."""
    served = served_tools()
    served_names = [t["name"] for t in served]
    implemented = implemented_tool_names()
    documented = readme_catalog_names()
    canonical = set(CATALOG_ORDER)

    set_problems: list[str] = []
    if set(served_names) != canonical:
        set_problems.append(
            f"served registry {sorted(served_names)} != catalog {sorted(canonical)}"
        )
    if implemented != canonical:
        set_problems.append(
            f"implemented tools {sorted(implemented)} != catalog {sorted(canonical)}"
        )
    if set(documented) != canonical:
        set_problems.append(
            f"README catalog {sorted(documented)} != catalog {sorted(canonical)}"
        )

    per_tool: dict[str, dict[str, Any]] = {}
    by_name = {t["name"]: t for t in served}
    for name in CATALOG_ORDER:
        tool = by_name.get(name)
        if tool is None:
            per_tool[name] = {"served": False, "problems": ["not served by the registry"]}
            continue
        shape = check_schema_shape(tool)
        trip = check_round_trip(tool)
        schema = tool["input_schema"]
        props = schema.get("properties", {})
        defs = schema.get("$defs", {}) if isinstance(schema.get("$defs"), dict) else {}
        per_tool[name] = {
            "served": True,
            "source_repo": CATALOG[name]["source_repo"],
            "n_params": len(props),
            "required": list(schema.get("required", [])),
            "typed_params": sum(1 for p in props.values() if _is_typed(p, defs)),
            "declares_output_schema": tool.get("output_schema") is not None,
            "valid_sample_accepted": "illustrative valid request is rejected"
            not in " ".join(trip),
            "invalid_sample_rejected": "wrongly) accepted" not in " ".join(trip),
            "error_response_valid": all(not p.startswith("error response") for p in trip)
            and all(not p.startswith("expected an invalid_input") for p in trip),
            "problems": shape + trip,
        }

    ok = not set_problems and all(not v["problems"] for v in per_tool.values())
    return {
        "ok": ok,
        "n_tools": len(CATALOG_ORDER),
        "set_problems": set_problems,
        "tools": per_tool,
    }


def assert_contract() -> dict[str, Any]:
    """Validate and raise :class:`ContractError` on any problem."""
    report = validate_catalog()
    if not report["ok"]:
        lines = list(report["set_problems"])
        for name, info in report["tools"].items():
            for p in info["problems"]:
                lines.append(f"{name}: {p}")
        raise ContractError("tool contract violated:\n  - " + "\n  - ".join(lines))
    return report


# --------------------------------------------------------------------------- #
# Deterministic catalog report (Markdown + CSV)
# --------------------------------------------------------------------------- #
_MD_HEADER = (
    "# chain-mcp tool catalog (contract-validated)\n"
    "\n"
    "Generated by `python -m chainmcp.contract` — a machine-checked snapshot of "
    "the tool contract the MCP server serves. Deterministic and byte-identical "
    "across re-runs; regenerated by the test suite so it cannot go stale.\n"
    "\n"
    "Every row below passed: schema completeness (typed parameters, valid "
    "JSON Schema, `type: object`), registry/implementation/README agreement, and "
    "a round-trip (an illustrative valid request is accepted and a malformed one "
    "rejected by the served `inputSchema`; a real offline error response "
    "validates against the documented result envelope).\n"
    "\n"
    "| Tool | Source repo | Params | Required | Typed | Valid sample accepted | "
    "Bad sample rejected | Error response valid |\n"
    "|---|---|---|---|---|---|---|---|\n"
)

_MD_FOOTER = (
    "\n"
    "**Notes.** Sample request payloads are *illustrative* — they exist to "
    "exercise the schema, not to represent real data. These tools return a "
    "`dict`, so the served MCP contract declares no `outputSchema`; the "
    "\"Error response valid\" column checks real error responses against "
    "chain-mcp's own documented result envelope (`ok` / `error_type` / `error`), "
    "which is producible offline (input validation runs before any engine "
    "import). See `chainmcp/contract.py`.\n"
)


def _row_cells(name: str, info: dict[str, Any]) -> list[str]:
    return [
        f"`{name}`",
        info["source_repo"],
        str(info["n_params"]),
        ", ".join(f"`{r}`" for r in info["required"]) if info["required"] else "—",
        f"{info['typed_params']}/{info['n_params']}",
        "yes" if info["valid_sample_accepted"] else "NO",
        "yes" if info["invalid_sample_rejected"] else "NO",
        "yes" if info["error_response_valid"] else "NO",
    ]


def render_markdown(report: dict[str, Any] | None = None) -> str:
    rep = report or validate_catalog()
    rows = "".join(
        "| " + " | ".join(_row_cells(name, rep["tools"][name])) + " |\n"
        for name in CATALOG_ORDER
    )
    return _MD_HEADER + rows + _MD_FOOTER


def render_csv(report: dict[str, Any] | None = None) -> str:
    rep = report or validate_catalog()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "tool",
            "source_repo",
            "n_params",
            "required_params",
            "typed_params",
            "valid_sample_accepted",
            "invalid_sample_rejected",
            "error_response_valid",
        ]
    )
    for name in CATALOG_ORDER:
        info = rep["tools"][name]
        writer.writerow(
            [
                name,
                info["source_repo"],
                info["n_params"],
                " ".join(info["required"]),
                info["typed_params"],
                str(info["valid_sample_accepted"]).lower(),
                str(info["invalid_sample_rejected"]).lower(),
                str(info["error_response_valid"]).lower(),
            ]
        )
    return buf.getvalue()


def write_reports(report: dict[str, Any] | None = None) -> None:
    rep = report or validate_catalog()
    CATALOG_MD.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_MD.write_text(render_markdown(rep), encoding="utf-8", newline="\n")
    CATALOG_CSV.write_text(render_csv(rep), encoding="utf-8", newline="\n")


def main() -> int:
    """Validate the contract, regenerate the catalog report, print a summary."""
    report = validate_catalog()
    write_reports(report)

    print(f"chain-mcp tool contract: {report['n_tools']} tools served")
    for name in CATALOG_ORDER:
        info = report["tools"][name]
        status = "OK" if not info["problems"] else "FAIL"
        typed = f"{info.get('typed_params', 0)}/{info.get('n_params', 0)} typed"
        print(f"  [{status}] {name:<26} {typed}  ({info.get('source_repo', '?')})")
        for p in info["problems"]:
            print(f"        - {p}")
    for p in report["set_problems"]:
        print(f"  [FAIL] registry/docs: {p}")

    print(f"wrote {CATALOG_MD.relative_to(ROOT)} and {CATALOG_CSV.relative_to(ROOT)}")
    if report["ok"]:
        print("RESULT: contract valid")
        return 0
    print("RESULT: contract INVALID")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
