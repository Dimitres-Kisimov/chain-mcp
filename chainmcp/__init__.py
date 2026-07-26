"""chain-mcp: an MCP (Model Context Protocol) server over my portfolio engines.

Exposes the optimization / analytics engines of my other repos (decision-chain,
logistics-digital-twin, route-optimizer, sales-kpi-analytics, portfolio-ops)
as MCP tools that any MCP client (Claude Desktop, Claude Code, ...) can call.

The engines are imported read-only from their source repositories via
``sys.path`` (paths configurable in :mod:`chainmcp.config`). Nothing in this
package modifies the source repos.

Author: Dimitres Kisimov.
"""

__version__ = "0.1.0"
