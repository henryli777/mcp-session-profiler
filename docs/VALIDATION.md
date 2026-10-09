# Validation evidence

The local showcase and the official MCP Python SDK workflows below were executed on 2026-10-09. These are stdio integration checks with controlled local tools; they do not establish Claude Desktop, Codex, or other application compatibility.

## Environment and scope

| Component | Executed version |
| --- | --- |
| Profiler | 0.1.0 source checkout |
| Python | CPython 3.13.7 |
| OS | macOS 27.0.1 (`Darwin`) |
| Official SDK distribution | `mcp==2.3.0` from PyPI |
| SDK protocol types | `mcp-types==2.3.0` |
| Negotiated protocol | `2025-11-25` |
| Transport | Local stdio, newline-delimited JSON-RPC |

The installed [official SDK](https://pypi.org/project/mcp/2.3.0/) identifies [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) as its repository. Its version registry knows `2026-07-28`, but `ClientSession.initialize()` offers the latest **handshake** revision, `2025-11-25`. Both measured SDK workflows negotiated `2025-11-25`. The `2026-07-28` discovery and per-request envelope workflow has not been tested here.

The SDK and its dependencies were installed in a disposable virtual environment outside the repository. They are verification dependencies; the profiler and the default fixture showcase require only Python's standard library.

## Executed workflows

1. The standard-library client in `examples/demo.py` launched `python -m mcp_session_profiler run`, initialized the fixture, listed its tools, and called `fast_echo`, `slow_echo`, `failing_tool`, and the optional unknown tool. The client checked Unicode text and both error forms, and the CLI produced JSON, a text summary, and offline HTML.
2. The official SDK's `stdio_client` and `ClientSession` launched the profiler around the real SDK `MCPServer` in `examples/sdk_check.py`. Initialization, listing, two successful calls, and an intentional tool error completed. Unicode text survived the relay; the profiler recorded three matching calls.
3. The same SDK client launched the profiler around the standard-library fixture. In addition to the three tools, the client received the expected SDK `MCPError` with code `-32602` for an unknown tool. The profiler recorded four matching calls and distinguished the RPC error from the tool error.

Every recorded session completed with server exit code 0 and complete observation. The fixture puts synthetic private markers in its argv, environment, arguments, successful result bodies, stderr, and error text. The executable showcase test checks that these markers are absent from both JSON and HTML. The SDK check also rejects a JSON report containing a marker.

The checked-in evidence below is the actual SDK checker stdout, with only benign version, protocol, and call metadata. It contains no command paths, environment values, result bodies, or raw error text:

- [SDK client to SDK server](validation/sdk-client-sdk-server.json)
- [SDK client to standard-library fixture](validation/sdk-client-fixture.json)

## Measured call metadata

These values come from one call of each tool in the saved SDK evidence. They demonstrate observation; they are not a benchmark, an overhead estimate, or a service latency target. The controlled slow tool sleeps for 30 ms, and scheduling and transport can add time.

| Server | Tool | Status | Latency ms | Complete response frame bytes |
| --- | --- | --- | ---: | ---: |
| SDK `MCPServer` | `fast_echo` | success | 6.809 | 148 |
| SDK `MCPServer` | `slow_echo` | success | 38.925 | 144 |
| SDK `MCPServer` | `failing_tool` | tool_error | 2.144 | 170 |
| Standard-library fixture | `fast_echo` | success | 0.171 | 148 |
| Standard-library fixture | `slow_echo` | success | 33.993 | 144 |
| Standard-library fixture | `failing_tool` | tool_error | 0.362 | 158 |
| Standard-library fixture | `unknown_tool` | rpc_error | 0.160 | 96 |

Response bytes cover the full JSON response frame, excluding its terminating newline. They are not model tokens or billing. Latency is measured at the proxy and includes transport and scheduling effects. No direct-versus-proxy overhead measurement has been made.

## Reproduce

Run these commands from a fresh checkout. Output files must not already exist.

```sh
python3 examples/demo.py --output-dir demo-output --include-rpc-error
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For the optional pinned SDK check, create a new virtual environment outside the checkout:

```sh
python3 -m venv /tmp/mcp-profiler-sdk-check
/tmp/mcp-profiler-sdk-check/bin/python -m pip install 'mcp==2.3.0'
/tmp/mcp-profiler-sdk-check/bin/python examples/sdk_check.py --output /tmp/mcp-profiler-sdk-session.json
/tmp/mcp-profiler-sdk-check/bin/python examples/sdk_check.py --server fixture --output /tmp/mcp-profiler-sdk-fixture-session.json
PYTHONPATH=src /tmp/mcp-profiler-sdk-check/bin/python -m unittest discover -s tests -v
```

The SDK integration test is skipped when the optional pinned SDK is absent. The checks use per-request deadlines and subprocess timeouts. The default profiler runtime never imports the SDK.

## Remaining limits

No HTTP transport, Windows execution, desktop host, sampling, elicitation, bidirectional server requests, cancellation through a real host, or production server has been validated by these examples. The observer and transport have separate automated checks for their correlation, bounds, byte forwarding, and shutdown contracts. Tool names and request IDs remain retained metadata and can be sensitive even when bodies are excluded.
