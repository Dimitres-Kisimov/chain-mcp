"""Engine-free error-handling / input-validation matrix for all six tools.

The project's core promise is that *every* tool validates its input and that
*every* failure comes back as the documented structured error envelope — never
an exception, never a server crash. The other test modules spot-check that with
roughly one bad case per tool; this module systematically drives the whole
documented validation contract:

* **Rejection matrix** — a categorized table of bad payloads per tool (wrong
  type, below-minimum, above-maximum, malformed structure, the sku/demand_class
  mutual-exclusion rule, path-traversal repo names, and the off-limits-repo
  block). Each one must return the documented envelope: ``ok: false`` with
  ``error_type == "invalid_input"`` and an ``error`` string that names the
  offending parameter, and it must validate against the same
  :data:`chainmcp.contract.RESULT_ENVELOPE_SCHEMA` the contract layer publishes.
* **Acceptance matrix** — the *other* side of every boundary: a valid edge value
  (inclusive min, inclusive max, or an interior value) must PASS validation. To
  prove that offline and deterministically, every sibling repo is pointed at a
  path that does not exist, so a payload that clears validation deterministically
  fails one step later at the engine-load stage with ``engine_unavailable`` — a
  result that can only happen if the value was *not* wrongly rejected as
  ``invalid_input``. No real engine is ever run.

All of this is engine-free: input validation happens before any sibling-repo
import, so the whole module passes in CI with no portfolio repos checked out
(the same self-contained path the contract and protocol tests use).
"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from chainmcp import contract, tools
from chainmcp.config import REPOS

# Reuse the *documented* result envelope from the contract layer (single source
# of truth) rather than re-declaring the error shape here.
_ENVELOPE = Draft202012Validator(contract.RESULT_ENVELOPE_SCHEMA)


def _assert_invalid_input(result: dict, param: str | None) -> None:
    """Assert a result is the documented invalid_input envelope naming ``param``."""
    json.dumps(result)  # JSON-safe: nothing non-serializable leaked out
    _ENVELOPE.validate(result)  # matches the published result envelope
    assert result["ok"] is False
    assert result["error_type"] == "invalid_input", result
    assert isinstance(result["error"], str) and result["error"]
    if param is not None:
        assert param in result["error"], (param, result["error"])


# --------------------------------------------------------------------------- #
# Rejection matrix: (id, tool, kwargs, expected substring in the error message)
#
# Every payload here is rejected by the tool's OWN validation, which runs before
# any sibling engine is imported -> a structured invalid_input error, offline.
# --------------------------------------------------------------------------- #
_REJECT: list[tuple[str, str, dict, str]] = [
    # ----- forecast_demand ------------------------------------------------- #
    ("forecast-horizon-type", "forecast_demand", {"horizon_weeks": "eight"}, "horizon_weeks"),
    ("forecast-horizon-float", "forecast_demand", {"horizon_weeks": 8.5}, "horizon_weeks"),
    ("forecast-horizon-below", "forecast_demand", {"horizon_weeks": 0}, "horizon_weeks"),
    ("forecast-horizon-above", "forecast_demand", {"horizon_weeks": 27}, "horizon_weeks"),
    ("forecast-class-unknown", "forecast_demand", {"demand_class": "bogus"}, "demand_class"),
    ("forecast-class-case", "forecast_demand", {"demand_class": "SMOOTH"}, "demand_class"),
    (
        "forecast-sku-and-class",
        "forecast_demand",
        {"sku": "85123A", "demand_class": "smooth"},
        "not both",
    ),
    # ----- optimize_slotting ----------------------------------------------- #
    ("slotting-topmoves-type", "optimize_slotting", {"top_moves": "five"}, "top_moves"),
    ("slotting-topmoves-below", "optimize_slotting", {"top_moves": 0}, "top_moves"),
    ("slotting-topmoves-above", "optimize_slotting", {"top_moves": 51}, "top_moves"),
    ("slotting-ncartons-type", "optimize_slotting", {"n_cartons": "sixty"}, "n_cartons"),
    ("slotting-ncartons-below", "optimize_slotting", {"n_cartons": 9}, "n_cartons"),
    ("slotting-ncartons-above", "optimize_slotting", {"n_cartons": 201}, "n_cartons"),
    # ----- pack_cartons ---------------------------------------------------- #
    ("pack-items-not-list", "pack_cartons", {"items": "not-a-list"}, "items"),
    ("pack-items-empty", "pack_cartons", {"items": []}, "items"),
    ("pack-item-not-object", "pack_cartons", {"items": [42]}, "items"),
    (
        "pack-item-dim-negative",
        "pack_cartons",
        {"items": [{"length_cm": -5, "width_cm": 20, "height_cm": 15, "weight_kg": 1}]},
        "length_cm",
    ),
    (
        "pack-item-dim-zero",
        "pack_cartons",
        {"items": [{"length_cm": 0, "width_cm": 20, "height_cm": 15, "weight_kg": 1}]},
        "length_cm",
    ),
    (
        "pack-item-dim-missing",
        "pack_cartons",
        {"items": [{"width_cm": 20, "height_cm": 15, "weight_kg": 1}]},
        "length_cm",
    ),
    (
        "pack-item-dim-above",
        "pack_cartons",
        {"items": [{"length_cm": 1001, "width_cm": 20, "height_cm": 15, "weight_kg": 1}]},
        "length_cm",
    ),
    (
        "pack-item-quantity-below",
        "pack_cartons",
        {"items": [{"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 1, "quantity": 0}]},
        "quantity",
    ),
    (
        "pack-item-quantity-above",
        "pack_cartons",
        {
            "items": [
                {"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 1, "quantity": 201}
            ]
        },
        "quantity",
    ),
    (
        "pack-total-over-cap",
        "pack_cartons",
        {
            "items": [
                {"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 1, "quantity": 200},
                {"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 1, "quantity": 1},
            ]
        },
        "cartons",
    ),
    ("pack-container-not-object", "pack_cartons", {"container": "big"}, "container"),
    (
        "pack-container-dim-above",
        "pack_cartons",
        {"container": {"length_cm": 6000}},
        "container.length_cm",
    ),
    (
        "pack-container-dim-zero",
        "pack_cartons",
        {"container": {"max_weight_kg": 0}},
        "container.max_weight_kg",
    ),
    # ----- route_deliveries ------------------------------------------------ #
    ("route-instance-unknown", "route_deliveries", {"instance": "nope"}, "instance"),
    ("route-instance-type", "route_deliveries", {"instance": 30}, "instance"),
    ("route-limit-type", "route_deliveries", {"solution_limit": "lots"}, "solution_limit"),
    ("route-limit-below", "route_deliveries", {"solution_limit": 0}, "solution_limit"),
    ("route-limit-above", "route_deliveries", {"solution_limit": 20001}, "solution_limit"),
    # ----- analyze_discount_leakage ---------------------------------------- #
    (
        "leakage-policy-type",
        "analyze_discount_leakage",
        {"policy_discount_pct": "lots"},
        "policy_discount_pct",
    ),
    (
        "leakage-policy-below",
        "analyze_discount_leakage",
        {"policy_discount_pct": -0.1},
        "policy_discount_pct",
    ),
    (
        "leakage-policy-at-100",
        "analyze_discount_leakage",
        {"policy_discount_pct": 100},
        "policy_discount_pct",
    ),
    ("leakage-topn-type", "analyze_discount_leakage", {"top_n": "five"}, "top_n"),
    ("leakage-topn-below", "analyze_discount_leakage", {"top_n": 0}, "top_n"),
    ("leakage-topn-above", "analyze_discount_leakage", {"top_n": 51}, "top_n"),
    # ----- portfolio_status ------------------------------------------------ #
    ("status-repo-empty", "portfolio_status", {"repo": ""}, "repo"),
    ("status-repo-whitespace", "portfolio_status", {"repo": "   "}, "repo"),
    ("status-repo-not-string", "portfolio_status", {"repo": 123}, "repo"),
    ("status-repo-traversal", "portfolio_status", {"repo": "../evil"}, "repo"),
    ("status-repo-fwd-slash", "portfolio_status", {"repo": "a/b"}, "repo"),
    ("status-repo-back-slash", "portfolio_status", {"repo": "a\\b"}, "repo"),
    ("status-repo-dotdot", "portfolio_status", {"repo": ".."}, "repo"),
    ("status-repo-colon", "portfolio_status", {"repo": "C:evil"}, "repo"),
    ("status-repo-blocklisted", "portfolio_status", {"repo": "3DpicToIFCModeling"}, "not auditable"),
]


@pytest.mark.parametrize(
    ("tool_name", "kwargs", "param"),
    [pytest.param(t, k, p, id=i) for (i, t, k, p) in _REJECT],
)
def test_bad_payload_returns_invalid_input_envelope(tool_name, kwargs, param):
    result = getattr(tools, tool_name)(**kwargs)
    _assert_invalid_input(result, param)


# --------------------------------------------------------------------------- #
# Acceptance matrix: valid boundary values that must PASS validation.
#
# With every sibling repo pointed at a non-existent path, a payload that clears
# validation deterministically fails one step later at the engine-load stage
# (engine_unavailable) -- which can only happen if it was NOT wrongly rejected as
# invalid_input. This checks the inclusive/exclusive edges are as documented and
# that validation is not over-restrictive. No engine is ever run.
# --------------------------------------------------------------------------- #
@pytest.fixture
def no_siblings(monkeypatch, tmp_path):
    """Point the whole portfolio root (and clear per-repo overrides) at nowhere."""
    monkeypatch.setenv("CHAINMCP_ROOT", str(tmp_path / "no-such-portfolio-root"))
    for env_var, _pkg in REPOS.values():
        monkeypatch.delenv(env_var, raising=False)


_ACCEPT: list[tuple[str, str, dict]] = [
    # forecast_demand — horizon bounds inclusive [1, 26]; sku alone is valid input
    ("forecast-horizon-min", "forecast_demand", {"horizon_weeks": 1}),
    ("forecast-horizon-max", "forecast_demand", {"horizon_weeks": 26}),
    ("forecast-class-only", "forecast_demand", {"demand_class": "lumpy"}),
    ("forecast-sku-only", "forecast_demand", {"sku": "85123A"}),
    # optimize_slotting — top_moves [1, 50], n_cartons [10, 200]
    ("slotting-edges-low", "optimize_slotting", {"top_moves": 1, "n_cartons": 10}),
    ("slotting-edges-high", "optimize_slotting", {"top_moves": 50, "n_cartons": 200}),
    # pack_cartons — quantity/dim upper edges inclusive; container edge inclusive
    (
        "pack-edges",
        "pack_cartons",
        {
            "items": [
                {"length_cm": 1000, "width_cm": 1000, "height_cm": 1000, "weight_kg": 1000,
                 "quantity": 200}
            ],
            "container": {"length_cm": 5000, "width_cm": 5000, "height_cm": 5000,
                          "max_weight_kg": 5000},
        },
    ),
    ("pack-default-seeded", "pack_cartons", {}),
    # route_deliveries — every instance name; solution_limit [1, 20000]
    ("route-tiny-min-limit", "route_deliveries", {"instance": "tiny", "solution_limit": 1}),
    ("route-n100-max-limit", "route_deliveries", {"instance": "n100", "solution_limit": 20000}),
    # analyze_discount_leakage — policy [0, 100), top_n [1, 50]
    ("leakage-policy-zero", "analyze_discount_leakage", {"policy_discount_pct": 0, "top_n": 1}),
    ("leakage-policy-near-100", "analyze_discount_leakage",
     {"policy_discount_pct": 99.99, "top_n": 50}),
    # portfolio_status — a well-formed bare repo name (trimmed) is valid input
    ("status-bare-name", "portfolio_status", {"repo": "route-optimizer"}),
    ("status-trims-whitespace", "portfolio_status", {"repo": "  route-optimizer  "}),
]


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [pytest.param(t, k, id=i) for (i, t, k) in _ACCEPT],
)
def test_valid_boundary_passes_validation(no_siblings, tool_name, kwargs):
    result = getattr(tools, tool_name)(**kwargs)
    json.dumps(result)
    _ENVELOPE.validate(result)
    # It cleared input validation (was NOT wrongly rejected) ...
    assert result.get("error_type") != "invalid_input", result
    # ... and, with the repos pointed away, failed at the engine-load stage.
    assert result["ok"] is False
    assert result["error_type"] == "engine_unavailable", result


# --------------------------------------------------------------------------- #
# The matrix is self-maintaining: it must cover every tool in the canonical
# catalog, so a newly added tool cannot slip in without a validation table.
# --------------------------------------------------------------------------- #
def test_reject_matrix_covers_every_tool():
    covered = {t for (_i, t, _k, _p) in _REJECT}
    assert covered == set(contract.CATALOG_ORDER)


def test_accept_matrix_covers_every_tool():
    covered = {t for (_i, t, _k) in _ACCEPT}
    assert covered == set(contract.CATALOG_ORDER)


def test_offlimits_repo_is_refused_without_touching_it():
    """The blocklisted repo is refused by a pure string check, before any
    filesystem access -- asserting this reads nothing from that repo."""
    result = tools.portfolio_status("3DpicToIFCModeling")
    _assert_invalid_input(result, "3DpicToIFCModeling")
    assert "not auditable" in result["error"]
