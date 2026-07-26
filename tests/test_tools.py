"""Direct tool-call tests (bypassing the MCP transport).

Every tool is exercised with valid input (structured, JSON-serializable, sane
numbers) and with invalid input (structured error result, never an exception).
The missing-repo path is exercised via the environment-variable override.
"""

from __future__ import annotations

import json

import pytest

from chainmcp import tools

pytestmark = pytest.mark.filterwarnings("ignore")


def _assert_json_safe(result: dict) -> None:
    json.dumps(result)  # raises if anything non-serializable leaked through


# --------------------------------------------------------------------------- #
# optimize_slotting
# --------------------------------------------------------------------------- #
def test_optimize_slotting_valid():
    r = tools.optimize_slotting(top_moves=5)
    assert r["ok"] is True
    assert "SYNTHETIC" in r["data_note"]
    assert r["optimized_travel_unit_m_per_day"] <= r["legacy_travel_unit_m_per_day"]
    assert r["reduction_pct"] > 0
    assert 1 <= len(r["top_moves_by_daily_saving"]) <= 5
    assert r["plan_verified"] is True
    # moves are ranked by daily saving, descending
    savings = [m["daily_saving_unit_m"] for m in r["top_moves_by_daily_saving"]]
    assert savings == sorted(savings, reverse=True)
    _assert_json_safe(r)


def test_optimize_slotting_rejects_bad_input():
    r = tools.optimize_slotting(n_cartons=0)
    assert r["ok"] is False
    assert r["error_type"] == "invalid_input"
    assert "n_cartons" in r["error"]


# --------------------------------------------------------------------------- #
# pack_cartons
# --------------------------------------------------------------------------- #
def test_pack_cartons_seeded_default():
    r = tools.pack_cartons()
    assert r["ok"] is True
    assert "SYNTHETIC" in r["data_note"] or "HEURISTIC" in r["data_note"]
    assert r["n_cartons"] == 60
    assert 0 < r["ffd_fill_rate"] <= 1.0
    assert r["ffd_containers"] < r["naive_baseline_containers"]
    assert sum(len(c["cartons"]) for c in r["assignment"]) + len(r["unplaced_cartons"]) == 60
    _assert_json_safe(r)


def test_pack_cartons_custom_items():
    items = [
        {"length_cm": 30, "width_cm": 20, "height_cm": 15, "weight_kg": 2.0, "quantity": 12},
        {"length_cm": 50, "width_cm": 40, "height_cm": 30, "weight_kg": 8.0, "quantity": 3},
    ]
    r = tools.pack_cartons(items=items)
    assert r["ok"] is True
    assert r["n_cartons"] == 15
    assert r["ffd_containers"] >= 1
    assert r["unplaced_cartons"] == []
    _assert_json_safe(r)


def test_pack_cartons_rejects_bad_items():
    r = tools.pack_cartons(items=[{"length_cm": -5, "width_cm": 20, "height_cm": 15, "weight_kg": 1}])
    assert r["ok"] is False
    assert r["error_type"] == "invalid_input"


# --------------------------------------------------------------------------- #
# route_deliveries
# --------------------------------------------------------------------------- #
def test_route_deliveries_beats_or_matches_baseline():
    r = tools.route_deliveries(instance="n30", solution_limit=100)
    assert r["ok"] is True
    assert "SYNTHETIC" in r["data_note"]
    assert r["feasible"] is True
    assert r["optimized_km"] <= r["baseline_clarke_wright_km"]
    assert r["vehicles_used"] == len(r["routes"])
    cap = r["instance"]["vehicle_capacity"]
    for route in r["routes"]:
        assert route["load"] <= cap
        assert route["stops"][0] == 0 and route["stops"][-1] == 0  # depot round trips
    _assert_json_safe(r)


def test_route_deliveries_is_deterministic():
    a = tools.route_deliveries(instance="tiny", solution_limit=50)
    b = tools.route_deliveries(instance="tiny", solution_limit=50)
    assert a["optimized_km"] == b["optimized_km"]
    assert a["routes"] == b["routes"]


def test_route_deliveries_rejects_unknown_instance():
    r = tools.route_deliveries(instance="n9999")
    assert r["ok"] is False
    assert r["error_type"] == "invalid_input"


# --------------------------------------------------------------------------- #
# analyze_discount_leakage
# --------------------------------------------------------------------------- #
def test_leakage_totals_reconcile():
    r = tools.analyze_discount_leakage(top_n=5)
    assert r["ok"] is True
    assert "SYNTHETIC" in r["data_note"]
    # within-policy + excess reconstructs the headline number (to the cent)
    assert (
        abs(r["within_policy_discount_eur"] + r["excess_discount_eur"] - r["total_leakage_eur"])
        < 0.02
    )
    assert 1 <= len(r["top_offenders_by_sales_rep"]) <= 5
    assert 1 <= len(r["top_offenders_by_region"]) <= 5
    # ranked worst-offender-first by excess
    excess = [x["excess_eur"] for x in r["top_offenders_by_sales_rep"]]
    assert excess == sorted(excess, reverse=True)
    _assert_json_safe(r)


def test_leakage_rejects_bad_policy():
    r = tools.analyze_discount_leakage(policy_discount_pct=150)
    assert r["ok"] is False
    assert r["error_type"] == "invalid_input"


# --------------------------------------------------------------------------- #
# forecast_demand  (slowest: runs the decision-chain ingest once, then cached)
# --------------------------------------------------------------------------- #
def test_forecast_demand_scoreboard_is_mase_honest():
    r = tools.forecast_demand()
    assert r["ok"] is True
    assert r["provenance"] == {"history": "real", "forecasts": "derived"}
    board = r["class_scoreboard"]
    assert len(board) > 0
    classes = {row["class"] for row in board}
    # every class shows ALL models with their MASE, not just the winner
    for cls in classes:
        rows = [b for b in board if b["class"] == cls]
        assert len(rows) >= 2
        assert sum(1 for b in rows if b["winner"]) == 1
        winner = next(b for b in rows if b["winner"])
        assert winner["mean_mase"] == min(b["mean_mase"] for b in rows)
    _assert_json_safe(r)


def test_forecast_demand_single_sku():
    summary = tools.forecast_demand(demand_class="smooth")
    sku = summary["demand_class"]["top_skus_by_forecast_units"][0]["sku"]
    r = tools.forecast_demand(sku=sku, horizon_weeks=4)
    assert r["ok"] is True
    assert r["sku"]["sku"] == sku
    assert len(r["sku"]["forecast"]) == 4
    assert all(f["units"] >= 0 for f in r["sku"]["forecast"])
    _assert_json_safe(r)


def test_forecast_demand_rejects_unknown_sku():
    r = tools.forecast_demand(sku="NOT-A-SKU")
    assert r["ok"] is False
    assert r["error_type"] == "invalid_input"


# --------------------------------------------------------------------------- #
# portfolio_status
# --------------------------------------------------------------------------- #
def test_portfolio_status_scorecard():
    r = tools.portfolio_status("route-optimizer", run_tests=False)
    assert r["ok"] is True
    assert r["exists"] is True
    assert 0 <= r["score_0_100"] <= 100
    assert isinstance(r["gaps"], list)
    assert r["tests_were_run"] is False
    _assert_json_safe(r)


def test_portfolio_status_rejects_paths():
    for bad in ("../evil", "a/b", "a\\b", ".."):
        r = tools.portfolio_status(bad)
        assert r["ok"] is False
        assert r["error_type"] == "invalid_input"


# --------------------------------------------------------------------------- #
# missing source repo -> clear structured error, not a crash
# --------------------------------------------------------------------------- #
def test_missing_repo_produces_clear_error(monkeypatch):
    monkeypatch.setenv("CHAINMCP_LOGISTICS_DIGITAL_TWIN", r"C:\definitely\not\here")
    r = tools.optimize_slotting()
    assert r["ok"] is False
    assert r["error_type"] == "engine_unavailable"
    assert "logistics-digital-twin" in r["error"]
    assert "CHAINMCP_LOGISTICS_DIGITAL_TWIN" in r["error"]
