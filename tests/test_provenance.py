"""Machine-readable result-provenance tests.

The feature under test: EVERY tool result — success or error — carries a
``provenance`` block (chainmcp/provenance.py) stating which server (name /
version / commit) served it, which tool produced it, which engine repo
(package, availability, commit) computed it, what the data was (a canonical
label, not free text), and whether the result is deterministic. The block is
attached centrally by the ``_safe`` wrapper, REQUIRED by the contract layer's
result envelope, and its registry is cross-checked against the served catalog.

Split as elsewhere: error-path and schema tests are engine-free and run
everywhere (CI included); success-path tests exercise real engines and skip
with a reason where the sibling repos are absent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import requires_repos
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from chainmcp import contract, provenance, tools
from chainmcp.config import REPOS

ROOT = Path(__file__).resolve().parents[1]

_PROV = Draft202012Validator(provenance.PROVENANCE_SCHEMA)
_ENVELOPE = Draft202012Validator(contract.RESULT_ENVELOPE_SCHEMA)
_SHA = re.compile(r"^[0-9a-f]{40,64}$")

TOOL_NAMES = list(contract.CATALOG_ORDER)


# --------------------------------------------------------------------------- #
# Registry consistency (self-maintaining: a new tool cannot skip provenance)
# --------------------------------------------------------------------------- #
def test_provenance_schema_is_itself_valid():
    Draft202012Validator.check_schema(provenance.PROVENANCE_SCHEMA)


def test_registry_covers_every_tool_exactly():
    assert set(provenance.TOOL_PROVENANCE) == set(contract.CATALOG_ORDER)


def test_registry_engine_repos_match_catalog_and_config():
    for name, spec in provenance.TOOL_PROVENANCE.items():
        assert spec["engine_repo"] == contract.CATALOG[name]["source_repo"], name
        assert spec["engine_repo"] in REPOS, name
        assert spec["data"]["label"] in provenance.DATA_LABELS, name
        assert isinstance(spec["deterministic"], bool), name


# --------------------------------------------------------------------------- #
# Error path (engine-free, runs everywhere): identity only, no data claims
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", TOOL_NAMES)
def test_error_result_carries_identity_only_provenance(name):
    bad_call = contract.CATALOG[name]["engine_free_bad_call"]
    result = getattr(tools, name)(**bad_call)
    assert result["ok"] is False
    json.dumps(result)  # JSON-safe

    prov = result["provenance"]
    _PROV.validate(prov)
    assert prov["schema_version"] == provenance.SCHEMA_VERSION
    assert prov["tool"] == name
    assert prov["server"]["name"] == "chain-mcp"
    assert prov["server"]["version"] == provenance.__version__
    assert prov["engine"]["repo"] == contract.CATALOG[name]["source_repo"]
    assert prov["engine"]["package"] == REPOS[prov["engine"]["repo"]][1]
    # nothing was computed -> no data claims on an error result
    assert "data" not in prov
    assert "deterministic" not in prov


def test_error_provenance_is_deterministic():
    a = tools.optimize_slotting(n_cartons=0)["provenance"]
    b = tools.optimize_slotting(n_cartons=0)["provenance"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_missing_repo_marks_engine_unavailable_in_provenance(monkeypatch):
    monkeypatch.setenv("CHAINMCP_LOGISTICS_DIGITAL_TWIN", r"C:\definitely\not\here")
    r = tools.optimize_slotting()
    assert r["error_type"] == "engine_unavailable"
    prov = r["provenance"]
    _PROV.validate(prov)
    assert prov["engine"]["available"] is False
    assert prov["engine"]["commit"] is None  # never a guessed SHA


# --------------------------------------------------------------------------- #
# Envelope enforcement: results WITHOUT provenance must FAIL validation
# --------------------------------------------------------------------------- #
def test_envelope_rejects_error_result_without_provenance():
    bare = {"ok": False, "error_type": "invalid_input", "error": "x"}
    with pytest.raises(ValidationError):
        _ENVELOPE.validate(bare)


def test_envelope_rejects_success_result_without_provenance():
    bare = {"ok": True, "data_note": "SYNTHETIC example"}
    with pytest.raises(ValidationError):
        _ENVELOPE.validate(bare)


def test_envelope_rejects_success_provenance_without_data_claims():
    # a success result must state its data facts and determinism
    identity_only = provenance.build_provenance("optimize_slotting", error=True)
    result = {"ok": True, "data_note": "SYNTHETIC example", "provenance": identity_only}
    with pytest.raises(ValidationError):
        _ENVELOPE.validate(result)


# --------------------------------------------------------------------------- #
# Builder unit behaviour (collapse-to-base-case, override rules, subset law)
# --------------------------------------------------------------------------- #
def test_build_without_override_collapses_to_registry_defaults():
    block = provenance.build_provenance("optimize_slotting")
    assert block["data"] == provenance.TOOL_PROVENANCE["optimize_slotting"]["data"]
    assert (
        block["deterministic"]
        == provenance.TOOL_PROVENANCE["optimize_slotting"]["deterministic"]
    )


def test_error_block_is_identity_subset_of_success_block():
    success = provenance.build_provenance("route_deliveries")
    error = provenance.build_provenance("route_deliveries", error=True)
    assert set(error) == {"schema_version", "server", "tool", "engine"}
    for key in error:
        assert error[key] == success[key]


def test_override_may_only_refine_data():
    with pytest.raises(ValueError, match="only refine 'data'"):
        provenance.build_provenance(
            "pack_cartons", override={"engine": {"repo": "somewhere-else"}}
        )


def test_unregistered_tool_is_a_loud_wiring_error():
    with pytest.raises(KeyError):
        provenance.build_provenance("no_such_tool")


# --------------------------------------------------------------------------- #
# git_head: best-effort facts, never guesses, never exceptions
# --------------------------------------------------------------------------- #
def test_git_head_returns_none_for_non_repo(tmp_path):
    assert provenance.git_head(tmp_path) is None
    assert provenance.git_head(tmp_path / "does-not-exist") is None


def test_git_head_returns_none_for_malformed_git_state(tmp_path):
    # .git pointer file with garbage content
    a = tmp_path / "a"
    a.mkdir()
    (a / ".git").write_text("not a gitdir pointer", encoding="utf-8")
    assert provenance.git_head(a) is None
    # .git directory whose HEAD is garbage
    b = tmp_path / "b"
    (b / ".git").mkdir(parents=True)
    (b / ".git" / "HEAD").write_text("gibberish", encoding="utf-8")
    assert provenance.git_head(b) is None


def test_git_head_reads_detached_and_symbolic_heads(tmp_path):
    sha = "a" * 40
    # detached HEAD: the SHA sits directly in HEAD
    det = tmp_path / "detached"
    (det / ".git").mkdir(parents=True)
    (det / ".git" / "HEAD").write_text(sha + "\n", encoding="utf-8")
    assert provenance.git_head(det) == sha
    # symbolic HEAD -> loose ref file
    sym = tmp_path / "symbolic"
    (sym / ".git" / "refs" / "heads").mkdir(parents=True)
    (sym / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (sym / ".git" / "refs" / "heads" / "main").write_text(sha + "\n", encoding="utf-8")
    assert provenance.git_head(sym) == sha
    # symbolic HEAD -> packed-refs fallback
    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (packed / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{sha} refs/heads/main\n",
        encoding="utf-8",
    )
    assert provenance.git_head(packed) == sha


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs the git binary and this checkout's .git to cross-check against",
)
def test_server_commit_matches_independent_git_oracle():
    """The self-reported chain-mcp commit equals what `git rev-parse HEAD` says."""
    expected = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    reported = tools.optimize_slotting(n_cartons=0)["provenance"]["server"]["commit"]
    assert reported == expected


# --------------------------------------------------------------------------- #
# Success path (engine-backed; skip with a reason where sibling repos absent)
# --------------------------------------------------------------------------- #
@requires_repos("logistics-digital-twin")
def test_success_result_carries_full_provenance():
    r = tools.optimize_slotting(top_moves=3)
    assert r["ok"] is True
    prov = r["provenance"]
    _PROV.validate(prov)
    assert prov["data"]["label"] == "synthetic"
    assert prov["deterministic"] is True
    assert prov["engine"]["available"] is True
    assert prov["engine"]["repo"] == "logistics-digital-twin"
    # the dev checkouts are git repos: the engine commit is a real SHA
    assert prov["engine"]["commit"] is not None
    assert _SHA.match(prov["engine"]["commit"])
    _ENVELOPE.validate(r)


@requires_repos("logistics-digital-twin")
def test_success_provenance_is_deterministic():
    a = tools.optimize_slotting(top_moves=3)["provenance"]
    b = tools.optimize_slotting(top_moves=3)["provenance"]
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


@requires_repos("logistics-digital-twin")
def test_pack_cartons_labels_caller_data_as_caller_provided():
    items = [{"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 2.0}]
    r = tools.pack_cartons(items=items)
    assert r["ok"] is True
    assert "_provenance" not in r  # the internal override key never leaks out
    prov = r["provenance"]
    _PROV.validate(prov)
    assert prov["data"]["label"] == "caller-provided"
    assert prov["data"]["source"] == "caller-provided item list"
    assert prov["deterministic"] is True  # FFD is deterministic on caller data too


@requires_repos("logistics-digital-twin")
def test_pack_cartons_seeded_default_stays_synthetic():
    r = tools.pack_cartons()
    assert r["ok"] is True
    assert r["provenance"]["data"]["label"] == "synthetic"


@requires_repos("portfolio-ops", "route-optimizer")
def test_portfolio_status_is_declared_non_deterministic():
    r = tools.portfolio_status("route-optimizer", run_tests=False)
    assert r["ok"] is True
    prov = r["provenance"]
    _PROV.validate(prov)
    assert prov["data"]["label"] == "real-local"
    assert prov["deterministic"] is False  # audits live repo state


@requires_repos("decision-chain")
def test_forecast_provenance_carries_per_field_labels():
    r = tools.forecast_demand(horizon_weeks=4)
    assert r["ok"] is True
    prov = r["provenance"]
    _PROV.validate(prov)
    assert prov["data"]["label"] == "real+derived"
    assert prov["data"]["fields"] == {"history": "real", "forecasts": "derived"}
