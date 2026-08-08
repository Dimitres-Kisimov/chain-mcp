"""The six tool implementations, callable directly (no MCP transport needed).

Every public function returns a plain JSON-safe dict and NEVER raises: engine
failures, bad input, and missing source repos all come back as a structured
error result (``{"ok": False, "error": ..., "error_type": ...}``), so the MCP
server cannot be crashed by a tool call.

Honesty contract
----------------
Each result carries a ``data_note`` saying what the numbers were computed on.
Five of the six engines run on the deterministic SYNTHETIC seeded datasets of
their source repos; the exception is ``forecast_demand``, whose source repo
(decision-chain) runs on the real UCI Online Retail II dataset with per-value
provenance tags. No result is presented as stronger than its inputs.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from chainmcp.config import EngineUnavailableError, ensure_repo, portfolio_root
from chainmcp.provenance import build_provenance

log = logging.getLogger("chainmcp.tools")


class InvalidInputError(ValueError):
    """Raised by the tools' own input validation (reported as invalid_input)."""


def _safe(fn):
    """Turn any exception into a structured error result — the server never crashes.

    Also attaches the machine-readable ``provenance`` block (see
    :mod:`chainmcp.provenance`) to EVERY result, success or error, so no tool
    can forget it. A tool may refine its data facts at call time by returning
    a ``_provenance`` override key (popped here, never serialized); error
    results carry identity only — no data claims, nothing was computed.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            result = fn(*args, **kwargs)
            override = result.pop("_provenance", None)
            result["provenance"] = build_provenance(fn.__name__, override=override)
            return result
        except EngineUnavailableError as e:
            log.warning("engine unavailable in %s: %s", fn.__name__, e)
            return {
                "ok": False,
                "error_type": "engine_unavailable",
                "error": str(e),
                "provenance": build_provenance(fn.__name__, error=True),
            }
        except InvalidInputError as e:
            return {
                "ok": False,
                "error_type": "invalid_input",
                "error": str(e),
                "provenance": build_provenance(fn.__name__, error=True),
            }
        except Exception as e:  # noqa: BLE001 - the whole point is: never crash
            log.exception("engine error in %s", fn.__name__)
            return {
                "ok": False,
                "error_type": "engine_error",
                "error": f"{type(e).__name__}: {e}",
                "provenance": build_provenance(fn.__name__, error=True),
            }

    return wrapper


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidInputError(message)


# --------------------------------------------------------------------------- #
# 1. forecast_demand  (decision-chain: real UCI data, MASE-honest CV)
# --------------------------------------------------------------------------- #
FORECAST_DATA_NOTE = (
    "History is REAL data (UCI Online Retail II, cleaned by the decision-chain repo's "
    "provenance-tagged pipeline); forecasts are DERIVED model output, selected per demand "
    "class by lowest mean MASE under rolling-origin cross-validation. If naive wins a "
    "class, naive is what gets reported and used."
)

_DEMAND_CLASSES = ("smooth", "erratic", "intermittent", "lumpy")
_forecast_cache: dict[str, Any] = {}


def _decision_chain_pipeline(horizon: int):
    """Cached stage 0 + stage 1 of decision-chain (ingest is the heavy part)."""
    ensure_repo("decision-chain")
    from chain import forecast as chain_forecast
    from chain import ingest as chain_ingest

    if "demand" not in _forecast_cache:
        raw = chain_ingest.load_raw()
        _cleaned, demand, _invoices = chain_ingest.run(raw)
        _forecast_cache["demand"] = demand
    demand = _forecast_cache["demand"]
    key = f"fc_{horizon}"
    if key not in _forecast_cache:
        _forecast_cache[key] = chain_forecast.run(demand, horizon=horizon)
    return demand, _forecast_cache[key]


@_safe
def forecast_demand(
    sku: str | None = None,
    demand_class: str | None = None,
    horizon_weeks: int = 8,
) -> dict:
    """Demand forecast from the decision-chain repo. See FORECAST_DATA_NOTE."""
    _require(isinstance(horizon_weeks, int) and 1 <= horizon_weeks <= 26,
             "horizon_weeks must be an integer between 1 and 26")
    _require(demand_class is None or demand_class in _DEMAND_CLASSES,
             f"demand_class must be one of {_DEMAND_CLASSES}")
    _require(sku is None or demand_class is None,
             "pass either sku or demand_class, not both")

    demand, fc = _decision_chain_pipeline(horizon_weeks)

    winners = [
        {
            "class": str(r["class"]),
            "model": str(r["model"]),
            "mean_mase": round(float(r["mean_mase"]), 4),
            "cv_cells": int(r["cells"]),
            "winner": bool(r["winner"]),
        }
        for _, r in fc.class_winners.iterrows()
    ]

    result: dict[str, Any] = {
        "ok": True,
        "data_note": FORECAST_DATA_NOTE,
        # per-field labels (history real / forecasts derived) now live in the
        # machine-readable provenance block: provenance.data.fields
        "horizon_weeks": horizon_weeks,
        "n_tracked_skus": len(demand.skus),
        "class_scoreboard": winners,
        "mase_note": (
            "MASE is scaled by the in-sample MAE of the one-week naive walk; "
            "values below 1 beat naive on that scale. The scoreboard shows every "
            "model per class, winner or not."
        ),
    }

    f = fc.forecasts
    if sku is not None:
        rows = f.loc[f["StockCode"] == sku]
        _require(len(rows) > 0,
                 f"unknown or ineligible SKU {sku!r} — it is not among the "
                 f"{len(demand.skus)} tracked SKUs with a forecast")
        first = rows.iloc[0]
        result["sku"] = {
            "sku": sku,
            "demand_class": str(first["Class"]),
            "model": str(first["Model"]),
            "sigma_units_per_week": round(float(first["Sigma"]), 2),
            "forecast": [
                {"week": str(r["Week"].date()), "units": round(float(r["Units"]), 2)}
                for _, r in rows.iterrows()
            ],
        }
    elif demand_class is not None:
        cls_rows = f.loc[f["Class"] == demand_class]
        _require(len(cls_rows) > 0, f"no forecast SKUs in class {demand_class!r}")
        totals = cls_rows.groupby("StockCode", observed=True)["Units"].sum().sort_values(
            ascending=False
        )
        result["demand_class"] = {
            "class": demand_class,
            "winner_model": str(cls_rows.iloc[0]["Model"]),
            "n_skus": int(totals.shape[0]),
            "total_forecast_units": round(float(totals.sum()), 1),
            "top_skus_by_forecast_units": [
                {"sku": str(s), "units_over_horizon": round(float(u), 1)}
                for s, u in totals.head(5).items()
            ],
        }
    else:
        counts = f.groupby("Class", observed=True)["StockCode"].nunique()
        result["summary"] = {
            "forecast_skus_by_class": {str(k): int(v) for k, v in counts.items()},
            "hint": "call again with a sku (e.g. one from class scoreboard) or a demand_class",
        }
    return result


# --------------------------------------------------------------------------- #
# 2. optimize_slotting  (logistics-digital-twin: seeded synthetic warehouse)
# --------------------------------------------------------------------------- #
SLOTTING_DATA_NOTE = (
    "SYNTHETIC seeded data: the deterministic warehouse (cartons, rack slots, ABC "
    "velocity demand) generated by my logistics-digital-twin repo (seed 42). No real "
    "warehouse is involved. Travel figures are demand-weighted unit-metres per day on "
    "that synthetic layout."
)


@_safe
def optimize_slotting(top_moves: int = 5, n_cartons: int = 60) -> dict:
    """Assignment-based slotting on the seeded warehouse. See SLOTTING_DATA_NOTE."""
    _require(isinstance(top_moves, int) and 1 <= top_moves <= 50,
             "top_moves must be an integer between 1 and 50")
    _require(isinstance(n_cartons, int) and 10 <= n_cartons <= 200,
             "n_cartons must be an integer between 10 and 200")

    ensure_repo("logistics-digital-twin")
    from logitwin.data import dataset
    from logitwin.slotting import slotting_report

    ds = dataset(n=n_cartons)
    rep = slotting_report(ds["cartons"], ds["warehouse"])

    landing_steps = [s for s in rep["sequence"] if s.to_slot is not None]
    landing_steps.sort(key=lambda s: -s.saving)
    moves = [
        {
            "sku": s.sku,
            "from_slot": s.from_slot if s.from_slot is not None else "staging",
            "to_slot": s.to_slot,
            "daily_saving_unit_m": round(float(s.saving), 2),
        }
        for s in landing_steps[:top_moves]
    ]

    return {
        "ok": True,
        "data_note": SLOTTING_DATA_NOTE,
        "method": "exact linear assignment (Hungarian / scipy linear_sum_assignment)",
        "n_cartons": n_cartons,
        "legacy_travel_unit_m_per_day": round(float(rep["legacy_travel"]), 1),
        "optimized_travel_unit_m_per_day": round(float(rep["optimized_travel"]), 1),
        "travel_saved_unit_m_per_day": round(float(rep["travel_saved"]), 1),
        "reduction_pct": round(float(rep["reduction_pct"]), 1),
        "n_moves": int(rep["n_moves"]),
        "n_physical_steps": int(rep["n_steps"]),
        "break_even_days": (
            round(float(rep["break_even_days"]), 1) if rep["break_even_days"] is not None else None
        ),
        "plan_verified": bool(rep["plan_valid"] and rep["sequence_valid"]),
        "top_moves_by_daily_saving": moves,
    }


# --------------------------------------------------------------------------- #
# 3. pack_cartons  (logistics-digital-twin: FFD heuristic packing)
# --------------------------------------------------------------------------- #
PACKING_DATA_NOTE = (
    "First-Fit-Decreasing shelf packing from my logistics-digital-twin repo — a "
    "HEURISTIC, not guaranteed optimal (bin packing is NP-hard). With no items given it "
    "packs the repo's SYNTHETIC seeded 60-carton dataset; with items given it packs "
    "exactly what you provide (no rotation of cartons is attempted)."
)

_MAX_ITEMS = 200


def _validate_items(items: list[dict]) -> list[dict]:
    _require(isinstance(items, list) and len(items) > 0, "items must be a non-empty list")
    expanded: list[dict] = []
    for i, it in enumerate(items):
        _require(isinstance(it, dict), f"items[{i}] must be an object")
        qty = it.get("quantity", 1)
        _require(isinstance(qty, int) and 1 <= qty <= _MAX_ITEMS,
                 f"items[{i}].quantity must be an integer between 1 and {_MAX_ITEMS}")
        dims = {}
        for k in ("length_cm", "width_cm", "height_cm", "weight_kg"):
            v = it.get(k)
            _require(isinstance(v, int | float) and 0 < float(v) <= 1000,
                     f"items[{i}].{k} must be a number in (0, 1000]")
            dims[k] = float(v)
        for _ in range(qty):
            expanded.append(dims)
    _require(len(expanded) <= _MAX_ITEMS, f"at most {_MAX_ITEMS} cartons total (after quantity)")
    return expanded


@_safe
def pack_cartons(items: list[dict] | None = None, container: dict | None = None) -> dict:
    """FFD carton packing for given items or the seeded dataset. See PACKING_DATA_NOTE."""
    # Validate ALL caller input before touching the engine repo: bad input must be
    # reported as invalid_input (fail fast), never masked by engine_unavailable.
    if container is not None:
        _require(isinstance(container, dict), "container must be an object")
        for k in ("length_cm", "width_cm", "height_cm", "max_weight_kg"):
            if k in container:
                _require(
                    isinstance(container[k], int | float) and 0 < float(container[k]) <= 5000,
                    f"container.{k} must be a number in (0, 5000]",
                )
    expanded = _validate_items(items) if items is not None else None

    ensure_repo("logistics-digital-twin")
    from logitwin.data import Carton, Container
    from logitwin.data import dataset as lt_dataset
    from logitwin.packing import ffd_pack, naive_pack

    if container is not None:
        box = Container(
            length=float(container.get("length_cm", 120.0)),
            width=float(container.get("width_cm", 80.0)),
            height=float(container.get("height_cm", 100.0)),
            max_weight=float(container.get("max_weight_kg", 300.0)),
        )
    else:
        box = Container()

    if expanded is None:
        cartons = lt_dataset()["cartons"]
        source = "synthetic seeded 60-carton dataset (logistics-digital-twin, seed 42)"
    else:
        cartons = [
            Carton(
                id=i,
                sku=f"ITEM-{i:03d}",
                length=d["length_cm"],
                width=d["width_cm"],
                height=d["height_cm"],
                weight=d["weight_kg"],
                velocity="B",
            )
            for i, d in enumerate(expanded)
        ]
        source = "caller-provided item list"

    ffd = ffd_pack(cartons, box)
    naive = naive_pack(cartons, box)

    packed_ids = {p.carton_id for c in ffd.containers for p in c.placements}
    unplaced = [c.sku for c in cartons if c.id not in packed_ids]

    result: dict[str, Any] = {
        "ok": True,
        "data_note": PACKING_DATA_NOTE,
        "items_source": source,
        "n_cartons": len(cartons),
        "container_cm": {
            "length": box.length,
            "width": box.width,
            "height": box.height,
            "max_weight_kg": box.max_weight,
        },
        "ffd_containers": int(ffd.n_containers),
        "ffd_fill_rate": round(float(ffd.fill_rate), 3),
        "naive_baseline_containers": int(naive.n_containers),
        "containers_saved_vs_naive": int(naive.n_containers - ffd.n_containers),
        "unplaced_cartons": unplaced,
        "assignment": [
            {
                "container": ci + 1,
                "cartons": [p.carton_id for p in c.placements],
                "used_volume_pct": round(100.0 * c.used_volume / box.volume, 1),
                "used_weight_kg": round(float(c.used_weight), 1),
            }
            for ci, c in enumerate(ffd.containers)
        ],
    }
    if expanded is not None:
        # Caller data, not the seeded dataset: say so in the machine-readable
        # provenance too, not just in the items_source text.
        result["_provenance"] = {
            "data": {"label": "caller-provided", "source": "caller-provided item list"}
        }
    return result


# --------------------------------------------------------------------------- #
# 4. route_deliveries  (route-optimizer: seeded synthetic CVRP)
# --------------------------------------------------------------------------- #
ROUTING_DATA_NOTE = (
    "SYNTHETIC seeded CVRP instances from my route-optimizer repo (clustered customers "
    "on a 100x100 grid read as km; deterministic, committed JSON). OR-Tools guided local "
    "search under a fixed solution limit (deterministic — same input, same answer) vs "
    "the classic Clarke-Wright savings heuristic a dispatcher could run by hand."
)

_INSTANCES = ("tiny", "n30", "n60", "n100")


def _solve_cvrp_solution_limit(instance, solution_limit: int):
    """CVRP solve under a deterministic solution limit.

    Adapted from my route-optimizer repo (routeopt/solver.py): identical model —
    PATH_CHEAPEST_ARC first solution, guided local search, capacity dimension —
    but terminated by ``solution_limit`` instead of a wall-clock limit, so the
    tool returns the same answer on every call. A generous time cap remains as
    a safety net only.
    """
    import time

    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    from routeopt.heuristic import Solution
    from routeopt.model import distance_matrix, scaled_int_matrix

    dist = distance_matrix(instance)
    idist = scaled_int_matrix(dist, 100)

    manager = pywrapcp.RoutingIndexManager(
        instance.num_nodes, instance.num_vehicles, instance.depot
    )
    routing = pywrapcp.RoutingModel(manager)

    def distance_cb(from_index: int, to_index: int) -> int:
        return int(idist[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)])

    transit_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

    def demand_cb(from_index: int) -> int:
        return int(instance.demands[manager.IndexToNode(from_index)])

    demand_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_idx, 0, [int(instance.capacity)] * instance.num_vehicles, True, "Capacity"
    )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.solution_limit = int(solution_limit)
    params.time_limit.FromSeconds(60)  # safety net only; the solution limit binds first

    t0 = time.perf_counter()
    assignment = routing.SolveWithParameters(params)
    elapsed = time.perf_counter() - t0
    if assignment is None:
        return Solution(
            method="or-tools",
            routes=[],
            total_distance=float("inf"),
            vehicles_used=0,
            loads=[],
            feasible=False,
            solve_time_s=elapsed,
        )

    routes: list[list[int]] = []
    for v in range(instance.num_vehicles):
        index = routing.Start(v)
        route = [manager.IndexToNode(index)]
        while not routing.IsEnd(index):
            index = assignment.Value(routing.NextVar(index))
            route.append(manager.IndexToNode(index))
        if len(route) > 2:
            routes.append(route)
    return Solution.from_routes(
        "or-tools", routes, dist, instance.demands, instance.capacity, solve_time_s=elapsed
    )


@_safe
def route_deliveries(instance: str = "n30", solution_limit: int = 500) -> dict:
    """Solve a seeded CVRP instance vs the savings baseline. See ROUTING_DATA_NOTE."""
    _require(instance in _INSTANCES, f"instance must be one of {_INSTANCES}")
    _require(isinstance(solution_limit, int) and 1 <= solution_limit <= 20000,
             "solution_limit must be an integer between 1 and 20000")

    repo = ensure_repo("route-optimizer")
    from routeopt.heuristic import clarke_wright
    from routeopt.model import distance_matrix, load_instance

    inst = load_instance(repo / "data" / "instances" / f"{instance}.json")
    dist = distance_matrix(inst)

    baseline = clarke_wright(inst, dist)
    solved = _solve_cvrp_solution_limit(inst, solution_limit)

    improvement = 0.0
    if baseline.total_distance > 0 and solved.feasible:
        improvement = 100.0 * (baseline.total_distance - solved.total_distance) / (
            baseline.total_distance
        )

    return {
        "ok": True,
        "data_note": ROUTING_DATA_NOTE,
        "instance": {
            "name": inst.name,
            "customers": int(inst.num_customers),
            "vehicle_capacity": int(inst.capacity),
            "fleet_size": int(inst.num_vehicles),
            "total_demand": int(inst.total_demand),
        },
        "baseline_clarke_wright_km": round(float(baseline.total_distance), 1),
        "optimized_km": round(float(solved.total_distance), 1) if solved.feasible else None,
        "km_saved": (
            round(float(baseline.total_distance - solved.total_distance), 1)
            if solved.feasible
            else None
        ),
        "improvement_pct": round(improvement, 1),
        "solution_limit": solution_limit,
        "feasible": bool(solved.feasible),
        "vehicles_used": int(solved.vehicles_used),
        "routes": [
            {"vehicle": i + 1, "stops": [int(n) for n in r], "load": int(load)}
            for i, (r, load) in enumerate(zip(solved.routes, solved.loads, strict=True))
        ],
    }


# --------------------------------------------------------------------------- #
# 5. analyze_discount_leakage  (sales-kpi-analytics: synthetic order data)
# --------------------------------------------------------------------------- #
LEAKAGE_DATA_NOTE = (
    "SYNTHETIC data: 24 months of deterministically generated B2B wholesale orders from "
    "my sales-kpi-analytics repo. Leakage = list value minus revenue (measured on that "
    "data); the within-policy vs excess split depends on an ASSUMED sanctioned-discount "
    "ceiling (default 10%) because the dataset carries no contract terms — the total "
    "leakage does not depend on it."
)


@_safe
def analyze_discount_leakage(policy_discount_pct: float = 10.0, top_n: int = 5) -> dict:
    """Discount-leakage drill-down by rep and region. See LEAKAGE_DATA_NOTE."""
    _require(isinstance(policy_discount_pct, int | float) and 0 <= policy_discount_pct < 100,
             "policy_discount_pct must be a number in [0, 100)")
    _require(isinstance(top_n, int) and 1 <= top_n <= 50,
             "top_n must be an integer between 1 and 50")

    ensure_repo("sales-kpi-analytics")
    from saleskpi.dataset import load as load_rows
    from saleskpi.spend import leakage_drilldown

    rows = load_rows()
    dd = leakage_drilldown(rows, policy_pct=float(policy_discount_pct) / 100.0)

    def _trim(recs: list[dict]) -> list[dict]:
        return [
            {k: v for k, v in r.items()}  # already JSON-safe (stdlib floats/strs)
            for r in recs[:top_n]
        ]

    return {
        "ok": True,
        "data_note": LEAKAGE_DATA_NOTE,
        "orders_analyzed": len(rows),
        "policy_discount_pct": dd["policy_discount_pct"],
        "policy_note": dd["policy_note"],
        "gross_list_value_eur": dd["gross_list_value_eur"],
        "net_revenue_eur": dd["net_revenue_eur"],
        "total_leakage_eur": dd["total_leakage_eur"],
        "within_policy_discount_eur": dd["within_policy_discount_eur"],
        "excess_discount_eur": dd["excess_discount_eur"],
        "top_offenders_by_sales_rep": _trim(dd["by_sales_rep"]),
        "top_offenders_by_region": _trim(dd["by_region"]),
    }


# --------------------------------------------------------------------------- #
# 6. portfolio_status  (portfolio-ops: read-only repo audit)
# --------------------------------------------------------------------------- #
STATUS_DATA_NOTE = (
    "Read-only audit from my portfolio-ops repo: git state, artifact presence, ruff, and "
    "(optionally) pytest, run against the local checkout of the named repo. It measures; "
    "it does not modify the repo."
)

# Off-limits regardless of configuration.
_STATUS_BLOCKLIST = {"3DpicToIFCModeling"}


@_safe
def portfolio_status(repo: str, run_tests: bool = False) -> dict:
    """Quality scorecard for one portfolio repo. See STATUS_DATA_NOTE."""
    _require(isinstance(repo, str) and repo.strip() != "", "repo must be a non-empty string")
    repo = repo.strip()
    _require(
        "/" not in repo and "\\" not in repo and repo not in (".", "..") and ":" not in repo,
        "repo must be a bare repository folder name (no path separators)",
    )
    _require(repo not in _STATUS_BLOCKLIST, f"repo {repo!r} is not auditable via this tool")

    ensure_repo("portfolio-ops")
    from ops.audit import audit_repo

    target = portfolio_root() / repo
    card = audit_repo(target, run_tests=bool(run_tests))

    if not card.get("exists"):
        return {
            "ok": True,
            "data_note": STATUS_DATA_NOTE,
            "repo": repo,
            "exists": False,
            "note": f"no folder named {repo!r} under {portfolio_root()}",
        }

    return {
        "ok": True,
        "data_note": STATUS_DATA_NOTE,
        "repo": repo,
        "exists": True,
        "score_0_100": int(card.get("score", 0)),
        "git": card.get("git", {}),
        "artifacts": card.get("artifacts", {}),
        "ruff_clean": bool(card.get("ruff", {}).get("clean", False)),
        "pytest": card.get("pytest", {}),
        "gaps": card.get("gaps", []),
        "tests_were_run": bool(run_tests),
    }
