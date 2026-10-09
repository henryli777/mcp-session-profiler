# v0.1 measurement contract

This executable alpha observes one client/server stdio connection on macOS or Linux. Python 3.11+ and the standard library are the runtime baseline. HTTP/SSE, Windows, A/B comparison, token estimation and automatic configuration remain future work.

## Inputs and persisted output

`mcp-profiler run --output NEW_PATH -- EXECUTABLE [ARGUMENTS...]` starts one process without a shell. The server inherits the runtime environment, but its executable, arguments and environment values are never placed in reports. Input/output is newline-delimited UTF-8 JSON-RPC; relay bytes are not rewritten. Server stderr is a separate stream, counted and discarded by default. Optional `--forward-stderr` exposes raw text only at runtime.

JSON schema version 1 contains:

| Field | Contract |
| --- | --- |
| `session` | Status, server exit code, duration, stderr bytes, Python version and platform |
| `calls` | Bounded client tool-call records: sequence, tool, typed ID, origin, start offset, outcome, nullable latency/response bytes/error code |
| `tool_calls_observed` | Recognized client tool-call request count; can exceed retained record count |
| `diagnostics` | Fixed categories and nonnegative counts, no raw error text |
| `coverage` | Observation completeness and configured limits |
| `traffic` | Bytes and newline-completed frames per direction; bytes include newlines |
| `summary` | Per-tool counts, matched latency sample counts/distributions, response bytes and errors from retained records |

No request parameters, result bodies, error messages/data, commands, environment values or raw stderr are persisted. Tool names and IDs may still contain private information. Reports are local, with no upload. `html` renders validated JSON using escaped text, inline CSS, no JavaScript/external assets, and a restrictive CSP. `summarize` escapes tool names for terminal output. Loaded summaries are rebuilt from call records.

The CLI retains at most 10,000 calls; the 64 MiB reader budget covers this bound even with escaped 256-byte tool names/IDs. Report files are exclusively created with mode 0600. Existing files and links are refused; no overwrite flag exists. Reserve the destination before server startup. A write failure returns exit code 2, and the reserved destination may remain incomplete. The user controls retention and deletion.

## Metrics

- **Frequency:** recognized client `tools/call` requests. Invalid/missing tool names do not receive invented names. Summaries distinguish observed and retained counts.
- **Latency:** monotonic elapsed time from reading the complete request frame to reading a matching response frame. Includes transport, scheduling, server execution and proxy effects; not server CPU time or task duration.
- **Size:** complete response frame bytes excluding LF, including JSON structure and any CR before LF. Never called token count or billing.
- **Outcome:** `success` is a JSON-RPC result; `tool_error` is a result with boolean `isError: true`; `rpc_error` is a JSON-RPC error envelope. These signals do not cover every application-level failure.
- **Statistics:** only matched calls contribute latency/response bytes. Nearest-rank quantiles use sorted value at `ceil(n*p)`, with n displayed. Unresolved and ambiguous calls have no matched metrics.

The observer parses transiently. Unobserved connections, hidden retries, actual model prompts, provider invoices and exact context are outside its visibility. Overhead is not guaranteed or subtracted from timings. The compatibility record is a protocol workflow, not proof of diagnosis value from independent users.

## Correlation and bounds

Correlation key = request origin + ID type + ID value. MCP IDs are strings or integers; booleans/null/floats are ineligible. Numeric `1` differs from string `"1"`. Server reverse requests cannot match client tool calls. Requests may complete out of order. Completed IDs may be reused when no ambiguity exists. Notifications/cancellation alone do not complete a request.

Duplicate active keys quarantine all related calls until shutdown. No arbitrary response assignment. Pending-key exhaustion stops new correlation admission for that session, preserving existing keys. Malformed, unsupported or unobserved frames can hide IDs; an unclassifiable frame quarantines all active calls and permanently disables new correlation for the session. Earlier completed samples stay valid; later recognized calls are unresolved. Orphan responses have no invented tool attribution.

Defaults: 1 MiB per observation frame, 10,000 retained calls, 20,000 pending keys, 256 UTF-8 bytes per tool name/string ID or decimal numeric ID. Each relay queue is at most 256 KiB, reads at most 64 KiB. OS pipe buffers and transient JSON objects add memory; these are bounds on retained data/queues, not an exact RSS guarantee. Oversized frames stream through when viable, but are excluded from observation; recovery starts at LF. Batches are forwarded without flattening and excluded from attribution. Truncated tails are not treated as completed frames.

## Lifecycle and exit codes

The proxy uses a POSIX process group. Client EOF starts a configurable shutdown grace (default 3 seconds). Child exit/stdout EOF, broken pipes, interruption and observer failures also trigger bounded shutdown. Server stderr EOF alone does not end an active connection. Connected idle sessions have no arbitrary idle deadline. Slow or blocked streams can exceed shutdown grace: the report records timeout/partial observation, and bytes still queued may not be delivered. Children deliberately leaving the process group are outside cleanup. Forced cleanup can add a 250 ms reap wait.

| Code | Meaning |
| --- | --- |
| 0 | Completed server, complete observation, no unresolved/ambiguous retained calls |
| 1 | Server failed |
| 2 | Invalid input/output or report failure |
| 3 | Transport/profiler failure |
| 4 | Partial observation or unresolved/ambiguous calls |
| 124 | Shutdown timeout |
| 130 | Interrupted |

A complete observation can contain unresolved requests: it means received frames were classified, not that every call completed. Session status, call status and observation coverage must be read together. Partial reports are saved where possible; SIGKILL or a failed filesystem cannot guarantee an artifact.

## Validation

The tests cover byte preservation, split Unicode, large frames/backpressure, typed/origin IDs, out-of-order/reused/duplicate IDs, observation limits, invalid frames, privacy sentinels, exclusive output, HTML escaping, EOF/exit/interrupt and descendant cleanup. [VALIDATION.md](VALIDATION.md) records actual official SDK versions/protocol. CLI install, local demo and SDK check can be rerun from README commands. A/B comparisons and independent user trials remain follow-up work.
