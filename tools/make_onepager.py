"""Build deliverables/chain_mcp_onepager.pdf -- the one-page project summary.

Pure matplotlib (no LaTeX, no external assets), deterministic layout on an A4
portrait canvas addressed in millimetres. ASCII-only stdout. Exits non-zero if
the rendered PDF is suspiciously small (<= 10 KB), so CI fails on a bad render.

Usage:
    python tools/make_onepager.py [--preview PNG_PATH]
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
OUT_PDF = ROOT / "deliverables" / "chain_mcp_onepager.pdf"
MIN_BYTES = 10 * 1024

# Fixed PDF metadata for a reproducible build: CreationDate=None drops the
# wall-clock timestamp, and pinned Creator/Producer make the bytes independent
# of the installed matplotlib version.
PDF_METADATA = {
    "Title": "chain-mcp one-pager",
    "Author": "Dimitres Kisimov",
    "Subject": "MCP server exposing portfolio optimization/analytics engines as tools",
    "Creator": "chain-mcp make_onepager.py",
    "Producer": "chain-mcp make_onepager.py",
    "CreationDate": None,
}

# A4 portrait in mm; the axes uses these as data coordinates (y grows downward).
PAGE_W, PAGE_H = 210.0, 297.0
MARGIN = 13.0

INK = "#1a2332"
MUTED = "#5a6675"
ACCENT = "#0e6e8e"
PANEL = "#f2f5f8"
CODE_BG = "#eef1f4"
SYN = "#8a5a00"   # synthetic-data tag
REAL = "#1e6f3e"  # real-data tag

MCP_PARAGRAPH = (
    "The Model Context Protocol (MCP) is an open standard, originated by Anthropic, for "
    "connecting AI applications to external tools and data. A server declares tools with typed "
    "JSON schemas; a client such as Claude Desktop discovers them through a JSON-RPC 2.0 "
    "handshake over stdio and calls them on the model's behalf. chain-mcp is a "
    "standard-conformant server built on the official mcp Python SDK. It is a working "
    "integration, not magic: the intelligence lives in the client's model, and the six tools "
    "below are ordinary, testable Python engines from my portfolio repositories."
)

TOOLS = [
    ("forecast_demand", "REAL data",
     "Weekly SKU demand forecasts (decision-chain). History is the real UCI Online Retail II "
     "dataset via a provenance-tagged pipeline; models picked per demand class by lowest mean "
     "MASE under rolling-origin CV -- if naive wins, naive is reported."),
    ("optimize_slotting", "SYNTHETIC seeded",
     "Warehouse slotting by exact linear assignment (Hungarian algorithm) on the seed-42 "
     "warehouse of logistics-digital-twin; travel before/after, ranked moves, break-even."),
    ("pack_cartons", "SYNTHETIC default",
     "First-Fit-Decreasing carton packing (a heuristic; bin packing is NP-hard, no rotation). "
     "Packs your item list, or the seeded 60-carton dataset when none is given."),
    ("route_deliveries", "SYNTHETIC seeded",
     "Capacitated vehicle routing on seeded instances (route-optimizer): OR-Tools guided local "
     "search under a fixed solution limit vs the Clarke-Wright savings baseline."),
    ("analyze_discount_leakage", "SYNTHETIC seeded",
     "Discount leakage on 24 months of generated B2B orders (sales-kpi-analytics); the "
     "within-policy vs excess split rests on an assumed 10% policy ceiling."),
    ("portfolio_status", "LOCAL audit",
     "Read-only quality scorecard for a local portfolio repo (portfolio-ops): git state, "
     "artifacts, ruff, optional pytest. Measures; never modifies."),
]

LIMITATIONS = [
    "Local-first by design: engines are imported read-only from sibling repos on this "
    "machine (paths via CHAINMCP_ROOT / per-repo env vars); a missing repo returns a "
    "structured engine_unavailable error, never a crash.",
    "Synthetic defaults: five of six engines run on deterministic seeded datasets; only "
    "forecast_demand uses real (UCI) history. Every result carries a data_note saying "
    "exactly what it was computed on.",
    "Single-user stdio server: one client per process, no auth, no network transport -- "
    "a portfolio integration piece, not a hardened multi-tenant service.",
]

CONFIG_SNIPPET = """\
// %APPDATA%\\Claude\\claude_desktop_config.json
{
  "mcpServers": {
    "chain-mcp": {
      "command": "python",
      "args": ["-m", "chainmcp"],
      "env": { "PYTHONPATH": "C:\\\\Users\\\\dimik\\\\chain-mcp" }
    }
  }
}"""


def wrap(s: str, width: int) -> str:
    return "\n".join(textwrap.wrap(s, width=width))


def box(ax, x, y, w, h, fc, ec, lw=0.8, rounding=1.6):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={rounding}",
        facecolor=fc, edgecolor=ec, linewidth=lw,
    ))


def section(ax, y, label):
    ax.text(MARGIN, y, label, fontsize=10.5, fontweight="bold", color=ACCENT,
            va="top", family="sans-serif")
    ax.plot([MARGIN, PAGE_W - MARGIN], [y + 5.4, y + 5.4], color=ACCENT, lw=0.6, alpha=0.5)


def draw(fig):
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAGE_W)
    ax.set_ylim(PAGE_H, 0)  # y grows downward, like a page
    ax.axis("off")

    # ---------------------------------------------------------------- header
    box(ax, 0, 0, PAGE_W, 24, INK, INK, rounding=0)
    ax.text(MARGIN, 8.2, "chain-mcp", fontsize=20, fontweight="bold", color="white",
            va="center", family="sans-serif")
    ax.text(MARGIN, 16.6,
            "A Model Context Protocol server exposing five portfolio optimization and "
            "analytics engines as six tools for any MCP client",
            fontsize=9, color="#cfd8e3", va="center")
    ax.text(PAGE_W - MARGIN, 8.2, "Dimitres Kisimov", fontsize=9, color="#cfd8e3",
            va="center", ha="right")
    ax.text(PAGE_W - MARGIN, 14.2, "stdio transport | official mcp SDK | 53 tests",
            fontsize=7.5, color="#9fb0c3", va="center", ha="right")

    # ------------------------------------------------------------ what MCP is
    y = 31.0
    section(ax, y, "WHAT THIS IS")
    ax.text(MARGIN, y + 8, wrap(MCP_PARAGRAPH, 122), fontsize=8, color=INK,
            va="top", linespacing=1.45)

    # ------------------------------------------------------------ tool catalog
    y = 66.0
    section(ax, y, "THE SIX TOOLS  (each result carries a data_note naming its data source)")
    row_y = y + 9.0
    row_h = 12.6
    for name, tag, desc in TOOLS:
        box(ax, MARGIN, row_y, PAGE_W - 2 * MARGIN, row_h - 1.6, PANEL, "#d7dee6", lw=0.5)
        ax.text(MARGIN + 3, row_y + 2.2, name, fontsize=8.6, fontweight="bold",
                family="monospace", color=INK, va="top")
        tag_color = REAL if tag.startswith("REAL") else (SYN if tag.startswith("SYN") else MUTED)
        ax.text(MARGIN + 3, row_y + 7.4, "[" + tag + "]", fontsize=7,
                fontweight="bold", color=tag_color, va="top", family="monospace")
        ax.text(MARGIN + 58, row_y + 2.0, wrap(desc, 92), fontsize=6.8,
                color="#33404f", va="top", linespacing=1.25)
        row_y += row_h

    # ------------------------------------------------------------ architecture
    y = 152.0
    section(ax, y, "ARCHITECTURE")
    ay = y + 10.0
    bh = 26.0

    def arch_box(x, w, title, lines, fc):
        box(ax, x, ay, w, bh, fc, "#c4ccd6", lw=0.7)
        ax.text(x + w / 2, ay + 4.6, title, fontsize=8, fontweight="bold",
                color=INK, ha="center", va="center")
        ax.text(x + w / 2, ay + 15.4, "\n".join(lines), fontsize=6.4, color=MUTED,
                ha="center", va="center", linespacing=1.45)

    arch_box(MARGIN, 44, "MCP client",
             ["Claude Desktop /", "Claude Code /", "any MCP client", "(the model lives here)"],
             "#ffffff")
    arch_box(88, 44, "chain-mcp server",
             ["FastMCP, official SDK", "typed tool schemas", "structured errors,",
              "never crashes"], PANEL)
    arch_box(163, PAGE_W - MARGIN - 163, "engine repos",
             ["decision-chain", "logistics-digital-twin", "route-optimizer",
              "sales-kpi-analytics + ops"], "#ffffff")

    def arrow(x0, x1):
        ax.add_patch(FancyArrowPatch((x0, ay + bh / 2), (x1, ay + bh / 2),
                                     arrowstyle="<->", mutation_scale=9,
                                     color=ACCENT, lw=1.1))

    arrow(57.5, 87.5)
    arrow(132.5, 162.5)
    ax.text(72.5, ay + bh / 2 - 3.4, "JSON-RPC 2.0", fontsize=6.2, color=ACCENT,
            ha="center", va="bottom")
    ax.text(72.5, ay + bh / 2 + 3.4, "over stdio", fontsize=6.2, color=ACCENT,
            ha="center", va="top")
    ax.text(147.5, ay + bh / 2 - 3.4, "read-only", fontsize=6.2, color=ACCENT,
            ha="center", va="bottom")
    ax.text(147.5, ay + bh / 2 + 3.4, "sys.path imports", fontsize=6.2, color=ACCENT,
            ha="center", va="top")

    # ------------------------------------------------------------- limitations
    y = 196.0
    section(ax, y, "HONEST LIMITATIONS")
    ly = y + 8.6
    for lim in LIMITATIONS:
        txt = wrap(lim, 120)
        ax.text(MARGIN + 1.5, ly, "-", fontsize=8, color=ACCENT, va="top",
                fontweight="bold")
        ax.text(MARGIN + 6, ly, txt, fontsize=7.4, color=INK, va="top", linespacing=1.4)
        ly += 4.1 * (txt.count("\n") + 1) + 2.6

    # ------------------------------------------------------ config + footer
    y = 240.0
    section(ax, y, "CONNECTING CLAUDE DESKTOP")
    cy = y + 7.5
    box(ax, MARGIN, cy, PAGE_W - 2 * MARGIN, 38, CODE_BG, "#c4ccd6", lw=0.6)
    ax.text(MARGIN + 4, cy + 3.0, CONFIG_SNIPPET, fontsize=7, family="monospace",
            color=INK, va="top", linespacing=1.4)

    ax.text(MARGIN, PAGE_H - 7.5,
            "Repo: chain-mcp (local) | Verify: python -m pytest (53 tests incl. live "
            "JSON-RPC handshake) + python -m ruff check .",
            fontsize=6.8, color=MUTED, va="center")
    ax.text(PAGE_W - MARGIN, PAGE_H - 7.5, "July 2026", fontsize=6.8, color=MUTED,
            va="center", ha="right")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the chain-mcp one-pager PDF.")
    parser.add_argument("--preview", metavar="PNG_PATH", default=None,
                        help="also write a PNG preview for visual inspection")
    args = parser.parse_args()

    OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
    draw(fig)
    # Deterministic metadata: no wall-clock CreationDate and version-independent
    # Creator/Producer, so re-runs produce a byte-identical PDF.
    fig.savefig(OUT_PDF, format="pdf", metadata=PDF_METADATA)
    if args.preview:
        fig.savefig(args.preview, format="png", dpi=150)
        print(f"[ok] preview written: {args.preview}")
    plt.close(fig)

    size = OUT_PDF.stat().st_size
    print(f"[ok] wrote {OUT_PDF} ({size} bytes)")
    if size <= MIN_BYTES:
        print(f"[fail] PDF is {size} bytes (<= {MIN_BYTES}); render looks broken")
        return 1
    print(f"[ok] size check passed (> {MIN_BYTES} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
