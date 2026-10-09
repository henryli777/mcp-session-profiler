#!/usr/bin/env python3
"""Small, deterministic MCP stdio server. No SDK or network is required.

The synthetic SECRET_SENTINEL values deliberately exercise report privacy.
They are sample data, not credentials. Stdout contains only JSON-RPC frames.
"""

import argparse
import json
import os
import sys
import time


PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL_VERSION}
TOOL_NAMES = ("fast_echo", "slow_echo", "failing_tool")
RESULT_SECRET = "RESULT_SECRET_SENTINEL"
ERROR_SECRET = "ERROR_SECRET_SENTINEL"


def result_for(request):
    """Return a protocol response, or None for a notification."""
    if "id" not in request:
        return None
    response = {"jsonrpc": "2.0", "id": request["id"]}
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        response["result"] = {
            "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-profiler-fixture", "version": "1.0.0"},
        }
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        response["result"] = {"tools": [
            {
                "name": name,
                "description": {
                    "fast_echo": "Echo text immediately, preserving Unicode.",
                    "slow_echo": "Echo text after a fixed 30 ms delay.",
                    "failing_tool": "Return an intentional MCP tool error.",
                }[name],
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "additionalProperties": False,
                },
            }
            for name in TOOL_NAMES
        ]}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            response["error"] = {"code": -32602, "message": f"Unknown tool: {ERROR_SECRET}"}
        else:
            arguments = params.get("arguments") or {}
            if name == "slow_echo":
                time.sleep(0.030)
            text = str(arguments.get("text", ""))
            if name == "failing_tool":
                text = f"Intentional tool failure: {ERROR_SECRET}"
            response["result"] = {
                "content": [{"type": "text", "text": f"{text} {RESULT_SECRET}"}],
                "isError": name == "failing_tool",
            }
    else:
        response["error"] = {"code": -32601, "message": f"Method not found: {ERROR_SECRET}"}
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-argv", default="ARGV_SECRET_SENTINEL", help=argparse.SUPPRESS)
    args = parser.parse_args()
    # This is deliberately sensitive-looking fixture data. The profiler counts
    # stderr bytes without writing the contents to JSON or HTML.
    print(
        "STDERR_SECRET_SENTINEL " + args.secret_argv + " "
        + os.environ.get("MCP_PROFILER_FIXTURE_SECRET", "ENV_SECRET_SENTINEL"),
        file=sys.stderr,
        flush=True,
    )
    for raw in sys.stdin.buffer:
        try:
            request = json.loads(raw)
            response = result_for(request)
        except (ValueError, TypeError, AttributeError):
            response = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"Invalid request: {ERROR_SECRET}"},
            }
        if response is not None:
            encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            sys.stdout.buffer.write(encoded + b"\n")
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
