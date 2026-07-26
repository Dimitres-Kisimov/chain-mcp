# Business case — agentic integration as the emerging enterprise pattern

## The situation

Enterprises already own the systems that hold their operational levers: WMS
slotting, load building, route planning, demand planning, margin analytics.
The bottleneck is rarely the algorithm — it is that using these systems takes a
specialist, a login, and an export-to-Excel round trip. Meanwhile, AI
assistants are becoming the front door through which people ask operational
questions ("why did pick travel go up?", "can we ship this order in two cages
instead of three?").

The pattern that connects the two is **tool use over an open protocol**: the
assistant does the conversation and the reasoning; the enterprise's own
engines do the computing; a protocol like MCP (modelcontextprotocol.io) does
the wiring. The assistant never invents a route or a fill rate — it calls the
solver and reads the structured result.

## What this project demonstrates

chain-mcp is a working, standard-conformant instance of that pattern, built on
engines I wrote myself rather than toy stubs:

- **Typed tool contracts.** Each tool declares a JSON schema; bad input is
  rejected with a structured error instead of a crash or a silent guess.
- **Honest results.** Every result carries a `data_note` (synthetic seeded
  data by default; real UCI retail data where the source repo has it) and
  provenance/assumption labels — the same discipline the source repos apply.
  An assistant can only be as honest as its tools' outputs.
- **Operational robustness.** A missing repo, an engine exception, or invalid
  arguments never take the server down; the error result tells the caller
  exactly what to fix.
- **Protocol hygiene.** stdout carries JSON-RPC frames only; logging goes to
  stderr; the handshake is covered by tests that drive a real subprocess.

## Why it plausibly matters commercially

I will not invent euro figures for a portfolio demo. The mechanism, though, is
concrete: the engines wrapped here quantify their own gaps on their own data —
the slotting solver reports a measured 44% travel reduction on its seeded
warehouse and prices the re-shuffle against a break-even; the CVRP solver
reports km saved against the heuristic a dispatcher would use; the leakage
drill-down decomposes a measured leakage total into named offenders. Whether
those numbers translate to a given real operation depends entirely on that
operation's data — which is precisely why the integration layer, not the demo
dataset, is the durable asset. Plugging a real WMS/TMS/ERP into the same tool
contracts is configuration and adapters, not a rewrite.

## What this is not

- Not a hosted product: local, single-user, stdio transport.
- Not a claim that an LLM should make operational decisions unsupervised: the
  tools return decision *support* (plans, savings, offender lists) that a
  human can inspect — every number traceable to a deterministic engine run.
- Not a benchmark of the engines themselves; each source repo carries its own
  validation (rolling-origin CV, exact-vs-heuristic gap checks, reconciliation
  identities).

(c) 2026 Dimitres Kisimov — all rights reserved; published for portfolio review.
