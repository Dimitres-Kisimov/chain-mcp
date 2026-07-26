"""MCP server wiring: stdio transport over the six portfolio-engine tools.

Implementation path: the official ``mcp`` Python SDK (FastMCP server class),
stdio transport. Protocol frames own stdout; ALL logging goes to stderr and is
kept ASCII-safe for the Windows console.

Start it with:  python -m chainmcp
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from chainmcp import tools

mcp = FastMCP(
    "chain-mcp",
    instructions=(
        "Optimization and analytics engines from Dimitres Kisimov's portfolio, exposed "
        "as MCP tools. Honesty note: unless a tool says otherwise, it runs on the "
        "deterministic SYNTHETIC seeded dataset of its source repo — the exception is "
        "forecast_demand, whose history is the real UCI Online Retail II dataset. Every "
        "result carries a data_note saying exactly what it was computed on."
    ),
)


class PackItem(BaseModel):
    """One carton (or a quantity of identical cartons) to pack."""

    length_cm: float = Field(gt=0, le=1000, description="Carton length in cm")
    width_cm: float = Field(gt=0, le=1000, description="Carton width in cm")
    height_cm: float = Field(gt=0, le=1000, description="Carton height in cm")
    weight_kg: float = Field(gt=0, le=1000, description="Carton weight in kg")
    quantity: int = Field(default=1, ge=1, le=200, description="How many identical cartons")


class PackContainer(BaseModel):
    """The container/pallet cage to pack into (defaults: 120x80x100 cm, 300 kg)."""

    length_cm: float = Field(default=120.0, gt=0, le=5000)
    width_cm: float = Field(default=80.0, gt=0, le=5000)
    height_cm: float = Field(default=100.0, gt=0, le=5000)
    max_weight_kg: float = Field(default=300.0, gt=0, le=5000)


@mcp.tool(
    description=(
        "Forecast weekly SKU demand with the decision-chain engine. History is REAL data "
        "(UCI Online Retail II, ~200 tracked SKUs) cleaned by a provenance-tagged pipeline; "
        "forecasts are DERIVED model output chosen per Syntetos-Boylan demand class by lowest "
        "mean MASE under rolling-origin cross-validation — MASE-honest: if naive wins a class, "
        "that is what gets reported. Pass a sku for its forecast, a demand_class (smooth / "
        "erratic / intermittent / lumpy) for the class view, or neither for the scoreboard. "
        "First call takes ~10 s (ingest); later calls are cached."
    )
)
def forecast_demand(
    sku: str | None = None,
    demand_class: Literal["smooth", "erratic", "intermittent", "lumpy"] | None = None,
    horizon_weeks: int = 8,
) -> dict:
    return tools.forecast_demand(sku=sku, demand_class=demand_class, horizon_weeks=horizon_weeks)


@mcp.tool(
    description=(
        "Optimize warehouse slotting on the SYNTHETIC seeded warehouse of my "
        "logistics-digital-twin repo (deterministic, seed 42 — no real warehouse data). "
        "Solves the exact linear-assignment slotting (Hungarian algorithm), and returns "
        "demand-weighted pick travel before/after, the re-shuffle move list ranked by daily "
        "saving, and a break-even analysis for executing it."
    )
)
def optimize_slotting(top_moves: int = 5, n_cartons: int = 60) -> dict:
    return tools.optimize_slotting(top_moves=top_moves, n_cartons=n_cartons)


@mcp.tool(
    description=(
        "Pack cartons into containers with First-Fit-Decreasing shelf packing from my "
        "logistics-digital-twin repo. FFD is a HEURISTIC (bin packing is NP-hard) and no "
        "carton rotation is attempted. Give a list of items (dims in cm, weight in kg, "
        "optional quantity) to pack your own cartons, or omit items to pack the repo's "
        "SYNTHETIC seeded 60-carton dataset. Returns fill rate, container count vs the "
        "one-carton-per-container baseline, and the per-container assignment."
    )
)
def pack_cartons(
    items: list[PackItem] | None = None,
    container: PackContainer | None = None,
) -> dict:
    return tools.pack_cartons(
        items=[i.model_dump() for i in items] if items is not None else None,
        container=container.model_dump() if container is not None else None,
    )


@mcp.tool(
    description=(
        "Solve a capacitated vehicle routing problem (CVRP) on a SYNTHETIC seeded instance "
        "from my route-optimizer repo (tiny / n30 / n60 / n100 customers; deterministic "
        "committed JSON, grid read as km). OR-Tools guided local search under a fixed "
        "solution limit — deterministic, same input gives the same answer — compared against "
        "the Clarke-Wright savings heuristic baseline. Returns km, the saving vs baseline, "
        "and the per-vehicle routes with loads."
    )
)
def route_deliveries(
    instance: Literal["tiny", "n30", "n60", "n100"] = "n30",
    solution_limit: int = 500,
) -> dict:
    return tools.route_deliveries(instance=instance, solution_limit=solution_limit)


@mcp.tool(
    description=(
        "Discount-leakage drill-down from my sales-kpi-analytics repo, on its SYNTHETIC "
        "24-month B2B order dataset (deterministically generated — no real customer data). "
        "Leakage = list value minus revenue; the within-policy vs excess split uses an "
        "ASSUMED sanctioned-discount ceiling (policy_discount_pct, default 10) because the "
        "dataset carries no contract terms. Returns totals plus the top offenders by sales "
        "rep and by region."
    )
)
def analyze_discount_leakage(policy_discount_pct: float = 10.0, top_n: int = 5) -> dict:
    return tools.analyze_discount_leakage(policy_discount_pct=policy_discount_pct, top_n=top_n)


@mcp.tool(
    description=(
        "Read-only quality scorecard for one local portfolio repo, via my portfolio-ops "
        "audit engine: git state, expected artifacts (README, LICENSE, CI, tests, business "
        "case, deliverables), ruff, and optionally pytest (run_tests=true is slow). This "
        "audits real local repositories on this machine; it measures and never modifies them."
    )
)
def portfolio_status(repo: str, run_tests: bool = False) -> dict:
    return tools.portfolio_status(repo=repo, run_tests=run_tests)


def main() -> None:
    """Entry point: configure stderr-only logging and serve on stdio."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("chainmcp").info("chain-mcp server starting (stdio transport)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
