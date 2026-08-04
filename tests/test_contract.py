"""Tool-contract / schema validation tests (self-contained: no sibling repos).

These check that the tool catalog the MCP server serves stays consistent with
the implementation and the docs, that every served schema is complete and
typed, and that a sample request/response round-trips against the real served
contract. Every check here runs offline — input validation happens before any
engine import — so these run everywhere, including CI with no portfolio repos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chainmcp import contract

ROOT = Path(__file__).resolve().parents[1]
TOOL_NAMES = list(contract.CATALOG_ORDER)


# --------------------------------------------------------------------------- #
# Registry vs implementation vs docs
# --------------------------------------------------------------------------- #
def test_served_registry_matches_catalog():
    served = {t["name"] for t in contract.served_tools()}
    assert served == set(contract.CATALOG_ORDER)


def test_implemented_functions_match_catalog():
    assert contract.implemented_tool_names() == set(contract.CATALOG_ORDER)


def test_readme_catalog_table_matches_catalog():
    documented = contract.readme_catalog_names()
    assert set(documented) == set(contract.CATALOG_ORDER)
    # no accidental duplicate rows
    assert len(documented) == len(set(documented)) == len(contract.CATALOG_ORDER)


# --------------------------------------------------------------------------- #
# Per-tool schema completeness / consistency
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_tool_schema_is_complete_and_typed(name):
    served = {t["name"]: t for t in contract.served_tools()}
    assert name in served, f"{name} is not served by the registry"
    problems = contract.check_schema_shape(served[name])
    assert problems == [], f"{name} schema problems: {problems}"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_parameter_is_typed(name):
    tool = {t["name"]: t for t in contract.served_tools()}[name]
    schema = tool["input_schema"]
    props = schema.get("properties", {})
    defs = schema.get("$defs", {}) if isinstance(schema.get("$defs"), dict) else {}
    assert props, f"{name} declares no parameters object"
    untyped = [p for p, s in props.items() if not contract._is_typed(s, defs)]
    assert untyped == [], f"{name} has untyped parameters: {untyped}"


# --------------------------------------------------------------------------- #
# Round-trip: illustrative request pair + real offline error response
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_round_trip(name):
    tool = {t["name"]: t for t in contract.served_tools()}[name]
    problems = contract.check_round_trip(tool)
    assert problems == [], f"{name} round-trip problems: {problems}"


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_error_response_matches_envelope(name):
    """A real, engine-free error response validates against the result envelope."""
    from jsonschema import Draft202012Validator

    from chainmcp import tools

    spec = contract.CATALOG[name]
    result = getattr(tools, name)(**spec["engine_free_bad_call"])
    json.dumps(result)  # JSON-safe
    Draft202012Validator(contract.RESULT_ENVELOPE_SCHEMA).validate(result)
    assert result["ok"] is False
    assert result["error_type"] == "invalid_input"


def test_result_envelope_schema_is_itself_valid():
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(contract.RESULT_ENVELOPE_SCHEMA)


# --------------------------------------------------------------------------- #
# Whole-catalog gate + deterministic report freshness
# --------------------------------------------------------------------------- #
def test_full_catalog_validates():
    report = contract.assert_contract()  # raises ContractError on any problem
    assert report["ok"] is True
    assert report["n_tools"] == len(contract.CATALOG_ORDER)
    assert report["set_problems"] == []


def test_committed_markdown_report_is_up_to_date():
    generated = contract.render_markdown()
    on_disk = contract.CATALOG_MD.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "deliverables/tool_catalog.md is stale — run `python -m chainmcp.contract`"
    )


def test_committed_csv_report_is_up_to_date():
    generated = contract.render_csv()
    on_disk = contract.CATALOG_CSV.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "deliverables/tool_catalog.csv is stale — run `python -m chainmcp.contract`"
    )


def test_report_generation_is_deterministic():
    assert contract.render_markdown() == contract.render_markdown()
    assert contract.render_csv() == contract.render_csv()
