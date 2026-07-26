# Credits and third-party notices

This repository is proprietary (see LICENSE), but it stands on permissively
licensed third-party libraries and on my own source repos, which it imports
read-only.

## Third-party libraries

| Library | License | Used for |
|---|---|---|
| [`mcp`](https://pypi.org/project/mcp/) (official Anthropic Model Context Protocol Python SDK) | MIT | the MCP server implementation (FastMCP, stdio transport, JSON-RPC framing). A permissively licensed dependency, compatible with this project's proprietary license; it remains under its own MIT terms. |
| `pydantic` | MIT | typed input schemas (pulled in by the SDK) |
| `numpy`, `pandas`, `scipy` | BSD | engine dependencies (forecasting, assignment slotting) |
| `ortools` (Google OR-Tools) | Apache-2.0 | CVRP routing solver |
| `pytest`, `ruff` | MIT | development gates only |

The `forecast_demand` tool's underlying dataset is the public
**UCI Online Retail II** dataset (UCI Machine Learning Repository), loaded and
cleaned by my decision-chain repo — see that repo's documentation for the full
data provenance story.

## Adapted from my own repos

The engines are imported from their source repositories via `sys.path`
(read-only; nothing here modifies them). One routine is adapted rather than
imported:

- `chainmcp/tools.py::_solve_cvrp_solution_limit` — adapted from my
  **route-optimizer** repo (`routeopt/solver.py`): identical routing model,
  but terminated by a deterministic solution limit instead of a wall-clock
  time limit, so repeated tool calls return identical answers.

Imported engines:

- **decision-chain** — `chain/ingest.py`, `chain/forecast.py` (real-data
  ingest + MASE-honest per-class forecasting)
- **logistics-digital-twin** — `logitwin/data.py`, `logitwin/slotting.py`,
  `logitwin/packing.py` (seeded warehouse, Hungarian slotting, FFD packing)
- **route-optimizer** — `routeopt/model.py`, `routeopt/heuristic.py`
  (instances, distance matrix, Clarke-Wright baseline)
- **sales-kpi-analytics** — `saleskpi/dataset.py`, `saleskpi/spend.py`
  (synthetic order data, leakage drill-down)
- **portfolio-ops** — `ops/audit.py` (read-only repo scorecards)

Author: Dimitres Kisimov.
