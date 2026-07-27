# chain-mcp — my portfolio engines as MCP tools

I have spent the past months building optimization and analytics engines —
slotting, packing, routing, forecasting, margin analytics — each living in its
own repo with its own CLI and web UI. This project is the agentic-integration
layer on top: an **MCP server** that exposes those real engines as tools, so an
AI assistant (Claude Desktop, Claude Code, or any other MCP client) can operate
them mid-conversation. You ask "optimize the slotting and tell me the top 5
moves", the assistant calls the actual Hungarian-algorithm solver in my
logistics-digital-twin repo, and reasons over the structured result it gets
back.

**What MCP is:** the [Model Context Protocol](https://modelcontextprotocol.io)
is an open standard (originated by Anthropic) for connecting AI applications to
external tools and data. A server declares tools with typed JSON schemas; a
client (the AI app) discovers them via a JSON-RPC handshake over stdio or HTTP
and calls them on the model's behalf. This repo is a standard-conformant server
implementation using the official `mcp` Python SDK — a working integration,
not magic: the intelligence is in the client's model, the engines are ordinary
deterministic code, and MCP is the wire between them.

**Why it matters for the portfolio:** wiring a language model to real,
non-trivial computational engines — with honest schemas, structured results,
provenance labels, and errors that never kill the server — is exactly the
integration work "agentic AI" projects consist of in practice.

## Honesty up front

Five of the six tools run on the **deterministic synthetic seeded datasets**
committed in their source repos — no real warehouse, fleet, or customer data is
involved, and every tool description and every result says so (`data_note`).
The one exception is `forecast_demand`: its source repo (decision-chain) runs
on the real, public **UCI Online Retail II** dataset through a provenance-tagged
pipeline, so its history is labelled `real` and its forecasts `derived`. No
result is presented as stronger than its inputs.

## Tool catalog

| Tool | Engine (source repo) | What it does | Data |
|---|---|---|---|
| `forecast_demand` | decision-chain (`chain/forecast.py`) | Weekly SKU demand forecasts, model chosen per Syntetos-Boylan demand class by lowest mean MASE under rolling-origin CV. If naive wins a class (it does, for lumpy), naive is what gets reported. | **Real** (UCI Online Retail II); forecasts derived |
| `optimize_slotting` | logistics-digital-twin (`logitwin/slotting.py`) | Exact linear-assignment slotting (Hungarian); travel before/after, ranked move list, break-even. | Synthetic seeded |
| `pack_cartons` | logistics-digital-twin (`logitwin/packing.py`) | First-Fit-Decreasing carton packing (a heuristic, and labelled as such) for your item list or the seeded 60-carton set; fill rate + assignment vs a naive baseline. | Synthetic seeded (or caller input) |
| `route_deliveries` | route-optimizer (`routeopt/`) | CVRP via OR-Tools guided local search under a **deterministic solution limit**, vs the Clarke-Wright savings baseline; km, saving, per-vehicle routes. | Synthetic seeded instances |
| `analyze_discount_leakage` | sales-kpi-analytics (`saleskpi/spend.py`) | Discount-leakage drill-down (list value minus revenue) with top offenders by rep and region; the within-policy/excess split against an assumed policy ceiling is labelled as an assumption. | Synthetic seeded |
| `portfolio_status` | portfolio-ops (`ops/audit.py`) | Read-only quality scorecard of a named local repo: git state, artifacts, ruff, optional pytest. Measures, never modifies. | Real local repos on this machine |

Every tool validates its input, and every failure — bad input, missing source
repo, engine error — comes back as a structured error result. The server does
not crash on a tool call.

## Setup

Requires Python 3.11+ and the source repos checked out locally (they are
imported read-only via `sys.path`; paths configurable, see below).

```
pip install mcp numpy pandas scipy ortools
```

Sanity check from the repo folder:

```
python -m chainmcp   # starts the server on stdio (Ctrl+C / close stdin to stop)
python -m pytest -q  # 21 tests, including a live JSON-RPC handshake
```

### Claude Desktop

Add to `claude_desktop_config.json` (Windows:
`%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "chain-mcp": {
      "command": "python",
      "args": ["-m", "chainmcp"],
      "env": { "PYTHONPATH": "C:\\Users\\dimik\\chain-mcp" }
    }
  }
}
```

(The `PYTHONPATH` entry makes `python -m chainmcp` importable from any working
directory; adjust the path if you cloned elsewhere.)

### Claude Code

```
claude mcp add chain-mcp --env PYTHONPATH=C:\Users\dimik\chain-mcp -- python -m chainmcp
```

(from inside the repo folder, a plain
`claude mcp add chain-mcp -- python -m chainmcp` also works).

### Configuring source-repo locations

By default the server looks for the source repos under `C:\Users\dimik`.
Override with environment variables (checked at call time):

| Variable | Meaning |
|---|---|
| `CHAINMCP_ROOT` | folder containing all portfolio repos |
| `CHAINMCP_DECISION_CHAIN`, `CHAINMCP_LOGISTICS_DIGITAL_TWIN`, `CHAINMCP_ROUTE_OPTIMIZER`, `CHAINMCP_SALES_KPI_ANALYTICS`, `CHAINMCP_PORTFOLIO_OPS` | per-repo overrides |

A missing repo produces a clear error result naming the path tried and the
variable to set — the server keeps running.

## Example prompts to try

1. *"Optimize the slotting and tell me the top 5 moves and what they save."*
2. *"Pack 40 cartons of 30x20x15 cm at 2 kg each and 10 of 55x35x30 cm at 9 kg
   into standard pallet cages — how many cages, and what fill rate?"*
3. *"Solve the 60-customer delivery instance and compare it with what a
   dispatcher's savings heuristic would produce. How many km does the optimizer
   save?"*
4. *"Which demand class is hardest to forecast, and which model wins it? Then
   forecast the top smooth-class SKU for the next 4 weeks."*
5. *"Where is discount margin leaking? Give me the top 3 sales reps by excess
   discount and say what part of the split is an assumption."*
6. *"Run a status audit of the route-optimizer repo — what's its score and what
   gaps are left?"*

## Limitations

- **Local paths.** The server imports engines from sibling checkouts on this
  machine; it is not a hosted service and is not packaged for distribution.
- **Synthetic defaults.** Except for `forecast_demand` (real UCI retail data)
  and `portfolio_status` (real local repos), the tools demonstrate the engines
  on their repos' seeded synthetic datasets. The numbers are real outputs of
  real solvers, on fabricated inputs.
- **Single user, stdio only.** One client per server process, no auth, no
  HTTP transport, no concurrency guarantees beyond what one stdio session
  needs.
- **First forecast call is slow** (~10 s: the decision-chain ingest runs once,
  then is cached in-process).

## Repo layout

```
chainmcp/
  config.py    # source-repo resolution (env-overridable), graceful failure
  tools.py     # the six tools: pure functions, structured results, never raise
  server.py    # FastMCP wiring, stdio transport, stderr-only logging
  __main__.py  # python -m chainmcp
tests/         # direct tool calls, validation, missing-repo, JSON-RPC handshake
docs/BUSINESS_CASE.md
CREDITS.md
```

(c) 2026 Dimitres Kisimov — all rights reserved; published for portfolio
review. See LICENSE. Third-party libraries remain under their own licenses
(see CREDITS.md).
