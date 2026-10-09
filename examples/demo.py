#!/usr/bin/env python3
"""Run a local MCP client through the profiler and produce JSON + offline HTML.

From a source checkout: python3 examples/demo.py --output-dir demo-output
No network, MCP SDK, model, or external MCP host is needed.
"""

import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).with_name("fixture_server.py")
PROTOCOL_VERSION = "2025-11-25"


def profiler_environment():
    """Permit both an installed package and a source checkout."""
    env = os.environ.copy()
    source = ROOT / "src"
    if source.is_dir():
        env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    env["MCP_PROFILER_FIXTURE_SECRET"] = "ENV_SECRET_SENTINEL"
    return env


class StdioClient:
    """Minimal request/response client with a deadline for every response."""

    def __init__(self, command, *, env, timeout=5.0):
        self.timeout = timeout
        self.buffer = bytearray()
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self.stderr, env=env, bufsize=0,
        )

    def send(self, message):
        wire = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        self.process.stdin.write(wire)
        self.process.stdin.flush()

    def request(self, request_id, method, params):
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            while b"\n" in self.buffer:
                line, _, remainder = self.buffer.partition(b"\n")
                self.buffer = bytearray(remainder)
                message = json.loads(line)
                if message.get("id") == request_id:
                    return message
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise TimeoutError(f"MCP response deadline exceeded for {method}")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("MCP transport closed before its response")
            self.buffer.extend(chunk)
            if len(self.buffer) > 1024 * 1024:
                raise RuntimeError("Example client response exceeds 1 MiB")

    def finish(self):
        self.process.stdin.close()
        status = self.process.wait(timeout=self.timeout)
        if status:
            raise RuntimeError(f"Profiler exited with status {status}")

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=self.timeout)
        for stream in (self.process.stdin, self.process.stdout, self.stderr):
            stream.close()


def run_session(output, *, include_rpc_error=False):
    """Exercise the executable relay; return only the negotiated version."""
    command = [
        sys.executable, "-m", "mcp_session_profiler", "run", "--output", str(output), "--",
        sys.executable, str(FIXTURE), "--secret-argv", "ARGV_SECRET_SENTINEL",
    ]
    client = StdioClient(command, env=profiler_environment())
    try:
        initialized = client.request(1, "initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "mcp-profiler-demo", "version": "1.0.0"},
        })
        version = initialized["result"]["protocolVersion"]
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        tools = client.request(2, "tools/list", {})
        if [tool["name"] for tool in tools["result"]["tools"]] != ["fast_echo", "slow_echo", "failing_tool"]:
            raise RuntimeError("Fixture tool listing is unexpected")
        fast = client.request(3, "tools/call", {
            "name": "fast_echo", "arguments": {"text": "你好 🌍 ARGUMENT_SECRET_SENTINEL"},
        })
        if "你好 🌍" not in fast["result"]["content"][0]["text"]:
            raise RuntimeError("Unicode response was not preserved")
        slow = client.request(4, "tools/call", {
            "name": "slow_echo", "arguments": {"text": "delayed ARGUMENT_SECRET_SENTINEL"},
        })
        failed = client.request(5, "tools/call", {"name": "failing_tool", "arguments": {}})
        if fast["result"]["isError"] or slow["result"]["isError"] or not failed["result"]["isError"]:
            raise RuntimeError("Fixture tool error result is unexpected")
        if include_rpc_error:
            unknown = client.request(6, "tools/call", {"name": "unknown_tool", "arguments": {}})
            if unknown["error"]["code"] != -32602:
                raise RuntimeError("Fixture RPC error result is unexpected")
        client.finish()
        return version
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("demo-output"), help="Directory for new session.json and report.html")
    parser.add_argument("--include-rpc-error", action="store_true", help="Also call an unknown tool to exercise JSON-RPC error handling")
    args = parser.parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "session.json"
    html = args.output_dir / "report.html"
    if output.exists() or html.exists():
        parser.error("Use a directory without an existing session.json or report.html")
    try:
        version = run_session(output, include_rpc_error=args.include_rpc_error)
        print(f"Fixture protocol negotiated: {version}", flush=True)
        subprocess.run(
            [sys.executable, "-m", "mcp_session_profiler", "summarize", str(output)],
            env=profiler_environment(), check=True, timeout=5,
        )
        subprocess.run(
            [sys.executable, "-m", "mcp_session_profiler", "html", str(output), "--output", str(html)],
            env=profiler_environment(), check=True, timeout=5,
        )
        print(f"JSON: {output}\nHTML: {html}")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Demo failed: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
