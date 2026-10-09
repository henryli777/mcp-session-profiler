#!/usr/bin/env python3
"""Optional compatibility check using the official mcp==2.3.0 Python SDK.

Install the SDK in a disposable virtual environment, then run this script there.
The profiler itself does not depend on mcp or anyio. The default workflow uses
the SDK's ClientSession and MCPServer on both sides of the executable relay.
"""

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import sys
import tempfile

from demo import FIXTURE, profiler_environment


def serve():
    """Serve real SDK tools; this mode is launched by the check itself."""
    import anyio
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError

    server = MCPServer("mcp-profiler-sdk-validation", version="1.0.0", log_level="ERROR")

    @server.tool(structured_output=False)
    def fast_echo(text: str = "") -> str:
        return text + " RESULT_SECRET_SENTINEL"

    @server.tool(structured_output=False)
    async def slow_echo(text: str = "") -> str:
        await anyio.sleep(0.030)
        return text + " RESULT_SECRET_SENTINEL"

    @server.tool(structured_output=False)
    def failing_tool() -> str:
        raise ToolError("Intentional tool failure: ERROR_SECRET_SENTINEL")

    print("STDERR_SECRET_SENTINEL " + os.environ.get("MCP_PROFILER_FIXTURE_SECRET", ""),
          file=sys.stderr, flush=True)
    server.run(transport="stdio")


async def exercise(output, server_kind):
    import anyio
    from mcp import ClientSession, MCPError, StdioServerParameters, stdio_client
    from mcp_types.version import LATEST_PROTOCOL_VERSION

    if server_kind == "fixture":
        server_args = [str(FIXTURE), "--secret-argv", "ARGV_SECRET_SENTINEL"]
    else:
        server_args = [str(Path(__file__).resolve()), "--serve"]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_session_profiler", "run", "--output", str(output), "--",
              sys.executable, *server_args],
        env=profiler_environment(),
    )
    # The SDK requires a real file handle for child stderr. No raw server stderr
    # or result bodies are emitted by this verification command.
    with tempfile.TemporaryFile(mode="w+") as errlog, anyio.fail_after(20):
        async with stdio_client(parameters, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=5.0) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                if names != ["fast_echo", "slow_echo", "failing_tool"]:
                    raise RuntimeError("The SDK did not list the expected tools")
                fast = await session.call_tool("fast_echo", {"text": "你好 🌍 ARGUMENT_SECRET_SENTINEL"})
                slow = await session.call_tool("slow_echo", {"text": "delayed ARGUMENT_SECRET_SENTINEL"})
                failed = await session.call_tool("failing_tool", {})
                preserved = any(getattr(content, "text", "").startswith("你好 🌍") for content in fast.content)
                if not preserved or fast.is_error or slow.is_error or not failed.is_error:
                    raise RuntimeError("The SDK did not preserve the expected tool results")
                if server_kind == "fixture":
                    try:
                        await session.call_tool("unknown_tool", {})
                    except MCPError as error:
                        if error.code != -32602:
                            raise RuntimeError("Unexpected SDK RPC error code") from None
                    else:
                        raise RuntimeError("The SDK did not surface the fixture RPC error")
                protocol = initialized.protocol_version
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    wanted = ["success", "success", "tool_error"]
    if server_kind == "fixture":
        wanted.append("rpc_error")
    if (not report["coverage"]["complete"] or report["session"]["status"] != "completed"
            or [call["status"] for call in report["calls"]] != wanted):
        raise RuntimeError("Profiler metadata does not match the completed SDK workflow")
    if "SECRET_SENTINEL" in report_text:
        raise RuntimeError("Synthetic private data leaked into the SDK session report")
    return {
        "sdk_distribution": "mcp",
        "sdk_version": importlib.metadata.version("mcp"),
        "mcp_types_version": importlib.metadata.version("mcp-types"),
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "os_version": platform.mac_ver()[0] or platform.release(),
        "workflow": "ClientSession.initialize / list_tools / call_tool",
        "server": "MCPServer" if server_kind == "sdk" else "stdlib fixture",
        "sdk_latest_known_protocol": LATEST_PROTOCOL_VERSION,
        "negotiated_protocol": protocol,
        "unicode_preserved": preserved,
        "coverage_complete": report["coverage"]["complete"],
        "session_status": report["session"]["status"],
        "server_exit_code": report["session"]["server_exit_code"],
        "tool_calls_observed": report["tool_calls_observed"],
        "calls": [{"tool": call["tool"], "status": call["status"],
                   "latency_ms": call["latency_ms"], "response_message_bytes": call["response_message_bytes"]}
                  for call in report["calls"]],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("sdk-session.json"), help="New profiler JSON destination")
    parser.add_argument("--server", choices=("sdk", "fixture"), default="sdk")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        import anyio
        installed = importlib.metadata.version("mcp")
    except (ImportError, importlib.metadata.PackageNotFoundError):
        print("Optional check requires mcp==2.3.0 in this Python environment.", file=sys.stderr)
        return 2
    if installed != "2.3.0":
        print("This reproducible check is pinned to mcp==2.3.0.", file=sys.stderr)
        return 2
    if args.serve:
        serve()
        return 0
    if args.output.exists():
        parser.error("The output destination already exists")
    try:
        result = anyio.run(exercise, args.output, args.server)
    except Exception as error:
        print(f"SDK check failed: {type(error).__name__}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
