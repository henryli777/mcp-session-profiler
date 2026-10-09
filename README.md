# mcp-session-profiler

[简体中文](README.zh-CN.md)

**Local diagnostics for actual MCP tool calls.** Put a stdio proxy between one MCP client and one server, then inspect which tools ran, which were slow or reported errors, and how large their responses were.

v0.1 is an **alpha** for macOS and Linux, Python 3.11+. Its runtime uses only the Python standard library. It forwards protocol bytes unchanged and saves bounded local metadata. Offline HTML reports need no JavaScript, external assets, or network connection.

![Executed local fixture report](showcases/local-fixture/report-preview.png)

## Try the local demo

```sh
git clone https://github.com/henryli777/mcp-session-profiler.git
cd mcp-session-profiler
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/demo.py --output-dir /tmp/mcp-profiler-demo
```

Choose a **new** output directory each time. The demo starts a local fixture, initializes MCP, discovers tools, calls fast/delayed/failing tools, and generates session.json and report.html. See the [executed local showcase](showcases/local-fixture/README.md) or open the generated HTML.

## Profile your server

Manually replace your stdio server launcher with:

```sh
mcp-profiler run --output /tmp/mcp-session-001.json -- your-server --your-server-option
```

The client connects to the profiler's stdin/stdout. Everything after `--` is the server executable and arguments, passed without a shell. This tool does not edit client configuration. Use absolute executable/report paths in client configuration and a fresh report path for each session. Existing files, symlinks and hardlinks are refused **before** starting the server.

After the connection closes:

```sh
mcp-profiler summarize /tmp/mcp-session-001.json
mcp-profiler html /tmp/mcp-session-001.json --output /tmp/mcp-session-001.html
```

`run` reserves stdout for protocol traffic. Server stderr is drained and discarded by default; its byte count is retained. `--forward-stderr` forwards raw text to runtime stderr and may expose secrets. Neither mode stores stderr text in report artifacts.

## What the report means

| Metric | Interpretation |
| --- | --- |
| Calls | Observed client `tools/call` requests; retained counts shown separately |
| Latency | Monotonic elapsed time between complete request/response frames at the proxy, including transport and proxy effects |
| Errors | Separate JSON-RPC errors and tool results with `isError: true` |
| Response bytes | Complete response JSON frame bytes excluding its newline, including JSON structure |
| Missing data | Unresolved/ambiguous calls, limits, malformed frames and incomplete sessions |

Only matched calls enter latency statistics, with sample counts and nearest-rank median/p95. A result does not prove business success. **Transport bytes are not tokens, model context or billing.** Only the instrumented connection is visible.

Correlation distinguishes number `1` from string `"1"`, separates client/server request origins, and handles out-of-order responses. Duplicate active IDs are quarantined until shutdown, without invented latency. JSON batches are forwarded but excluded from attribution. Malformed JSON, invalid UTF-8, oversized and truncated frames produce diagnostics.

Defaults: 1 MiB observation frame limit, 10,000 retained calls, 20,000 pending keys, 256-byte tool names/IDs, bounded relay queues. Adjust with `--max-frame-bytes`, `--max-calls` (1–10,000), `--max-pending`. Reports up to 64 MiB can be loaded. Oversized frames are still streamed when viable, with partial observation. Unclassifiable frames quarantine active calls and disable correlation for the rest of the session; earlier completed samples remain valid. Later recognized calls are unresolved. Pending-key exhaustion stops admission of new correlation keys for the rest of that session.

After client input EOF the server gets 3 seconds to finish (`--shutdown-timeout`, 0.05–60 seconds). Increase this for slow calls. Interruption/forced shutdown cleans up the server process group. The proxy changes timing and cannot guarantee every client's shutdown behavior.

## Privacy

Reports retain tool names, typed request IDs, timings, bytes, numeric error codes, outcomes, platform/Python versions and fixed diagnostic categories. They exclude argument/result bodies, error messages/data, launch arguments, environment values and raw stderr. Messages are parsed transiently in memory. **Tool names and IDs can themselves be sensitive**; review reports before sharing. Files use mode `0600`. No telemetry or uploads.

## Validation and development

[Validation evidence](docs/VALIDATION.md) records the official Python SDK workflow, exact versions and observed protocol. This does not establish Claude Code or Codex compatibility.

```sh
python -m unittest discover -s tests -v
python -m pip install 'mcp==2.3.0'  # verification dependency only
python examples/sdk_check.py --output /tmp/mcp-profiler-sdk-check.json
```

Exit codes: `0` completed with full observation and no unresolved/ambiguous retained calls; `1` server failed; `2` input/output/report failure; `3` transport/profiler failure; `4` partial observation or unresolved/ambiguous calls; `124` shutdown timeout; `130` interruption. Partial reports are finalized where possible; report-write failure is reported on stderr and returns `2`.

HTTP/SSE, Windows, automatic client configuration, A/B comparison, token estimation and automatic tuning are outside v0.1. See [measurement contract](docs/MVP.md), [roadmap](ROADMAP.md), and [contributing](CONTRIBUTING.md).

Static schema counting is covered by [mcp-tokens](https://github.com/sd2k/mcp-tokens) and [mcp-token-audit](https://github.com/michaeltuszynski/mcp-token-audit). This project focuses on an **actual session**. Historical [Claude Code issue #29995](https://github.com/anthropics/claude-code/issues/29995) informed the initial research; it is not evidence of current client behavior or savings.

MIT — [LICENSE](LICENSE).
