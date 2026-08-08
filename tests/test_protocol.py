"""JSON-RPC handshake tests over the real stdio transport.

Spawns ``python -m chainmcp`` as a subprocess, writes newline-delimited
JSON-RPC to its stdin (initialize -> initialized -> tools/list -> tools/call),
closes stdin, and asserts on the frames that came back on stdout — including
that stdout carries protocol frames ONLY (no stray prints).
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import requires_repos

ROOT = Path(__file__).resolve().parents[1]

_REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "chain-mcp-tests", "version": "0.1.0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "analyze_discount_leakage", "arguments": {"top_n": 3}},
    },
    # Self-contained tools/call: input validation rejects this before any engine
    # import, so the round-trip works even where the sibling repos are absent (CI).
    {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "analyze_discount_leakage", "arguments": {"policy_discount_pct": 150}},
    },
]


@pytest.fixture(scope="module")
def stdio_session() -> tuple[list[str], dict[int, dict]]:
    """One server run: requests written one at a time, each response awaited.

    A reader thread drains stdout into a queue so the main thread can wait for
    each response with a timeout (Windows pipes have no non-blocking readline).
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "chainmcp"],
        cwd=str(ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
    )
    out_q: queue.Queue[str] = queue.Queue()

    def _drain() -> None:
        for line in proc.stdout:  # ends at EOF when the server exits
            if line.strip():
                out_q.put(line)

    reader = threading.Thread(target=_drain, daemon=True)
    reader.start()

    lines: list[str] = []
    by_id: dict[int, dict] = {}
    try:
        for req in _REQUESTS:
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            if "id" not in req:
                continue  # notifications get no response
            deadline_lines = None
            while deadline_lines is None:
                ln = out_q.get(timeout=60)
                lines.append(ln)
                msg = json.loads(ln)
                if isinstance(msg.get("id"), int):
                    by_id[msg["id"]] = msg
                    if msg["id"] == req["id"]:
                        deadline_lines = ln
        proc.stdin.close()
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    return lines, by_id


def test_initialize_handshake(stdio_session):
    _lines, by_id = stdio_session
    init = by_id[1]
    assert init["jsonrpc"] == "2.0"
    assert "result" in init
    assert init["result"]["serverInfo"]["name"] == "chain-mcp"
    assert "protocolVersion" in init["result"]


def test_tools_list_carries_honesty_labels(stdio_session):
    _lines, by_id = stdio_session
    listing = by_id[2]["result"]["tools"]
    names = {t["name"] for t in listing}
    assert names == {
        "forecast_demand",
        "optimize_slotting",
        "pack_cartons",
        "route_deliveries",
        "analyze_discount_leakage",
        "portfolio_status",
    }
    for t in listing:
        assert t["inputSchema"]["type"] == "object"
        # every description states what data the tool runs on
        desc = t["description"]
        assert ("SYNTHETIC" in desc) or ("REAL data" in desc) or ("real local" in desc), t["name"]


@requires_repos("sales-kpi-analytics")  # the valid call runs the real leakage engine
def test_tools_call_returns_structured_result(stdio_session):
    _lines, by_id = stdio_session
    result = by_id[3]["result"]
    assert result.get("isError") is not True
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["total_leakage_eur"] > 0
    assert len(payload["top_offenders_by_sales_rep"]) == 3
    assert "SYNTHETIC" in payload["data_note"]


def test_tools_call_invalid_input_is_structured_error(stdio_session):
    """Engine-free tools/call round-trip: validation rejects before any engine import."""
    _lines, by_id = stdio_session
    result = by_id[4]["result"]
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert payload["error_type"] == "invalid_input"
    assert "policy_discount_pct" in payload["error"]


def test_tools_call_response_carries_provenance_over_the_wire(stdio_session):
    """The machine-readable provenance block survives the real stdio transport.

    Uses the engine-free error response (id=4), so this runs everywhere,
    including CI with no sibling repos: even a failed call states which
    server / tool / engine it came from.
    """
    from jsonschema import Draft202012Validator

    from chainmcp.provenance import PROVENANCE_SCHEMA

    _lines, by_id = stdio_session
    result = by_id[4]["result"]
    payload = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    prov = payload["provenance"]
    Draft202012Validator(PROVENANCE_SCHEMA).validate(prov)
    assert prov["tool"] == "analyze_discount_leakage"
    assert prov["server"]["name"] == "chain-mcp"
    assert prov["engine"]["repo"] == "sales-kpi-analytics"


def test_stdout_is_protocol_frames_only(stdio_session):
    lines, _by_id = stdio_session
    assert len(lines) >= 3
    for ln in lines:
        msg = json.loads(ln)  # every stdout line must be JSON ...
        assert msg.get("jsonrpc") == "2.0"  # ... and a JSON-RPC frame
