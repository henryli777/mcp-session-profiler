# v0.1 implementation contract

Deliver an installable Python 3.11+ standard-library CLI, a transparent POSIX stdio relay, bounded metadata observation, JSON and offline HTML reports, and a reproducible local showcase. Manual A/B comparison and HTTP transport remain future work.

Interfaces:

- `Observer(max_frame_bytes=1048576, max_calls=10000, max_pending=20000, clock=time.monotonic)`: `feed(direction, bytes)` for `client` or `server`; `diagnostic(category, count=1)`; `finish()`; `snapshot()` returns `{calls, diagnostics, coverage, traffic, tool_calls_observed}`. One event-loop thread owns the observer.
- Calls have `sequence`, `tool`, `request_id: {type, value}`, `origin`, `started_ms`, `status`, and nullable `latency_ms`, `response_message_bytes`, `error_code`. Statuses: `pending`, `success`, `tool_error`, `rpc_error`, `ambiguous`, `unresolved`. Only unambiguous matched responses contribute latency. ID type/value and origin are independent correlation keys.
- Coverage has `complete: bool` and `limits` describing limits. Diagnostics are a dictionary of fixed categories to counts. Traffic contains bytes and completed frames per direction. Bodies, raw error text, commands, environment, and raw stderr must never enter snapshot.
- `run_proxy(command, observer, *, shutdown_timeout=3.0, forward_stderr=False)` returns `{status, server_exit_code, stderr_bytes}`. Statuses: `completed`, `server_failed`, `interrupted`, `transport_failed`, `shutdown_timeout`. Forward bytes unchanged; bound all relay queues and observation buffers; stdout is exclusively server protocol. Launch argv without a shell and supervise a process group. Supported release platforms: macOS and Linux.
- Root CLI composes `{schema_version: 1, profiler_version, session: {status, server_exit_code, duration_ms, stderr_bytes, python_version, platform}, ...snapshot, summary}`. It writes JSON to an explicit new local path using exclusive creation, mode 0600; `html` renders that JSON to a separate new path. Existing destinations are refused. `summarize` prints a text table.
- Summary uses retained calls and reports observed/retained counts separately. Per tool: calls, matched, unresolved, ambiguous, rpc_errors, tool_errors, response_bytes, latency `{samples, min_ms, median_ms, p95_ms, max_ms}`. Quantiles use nearest rank. Tool names and typed IDs are bounded metadata and may still be sensitive.

Implementation steps: observer and tests; relay/lifecycle and tests; CLI/reports/privacy tests; fixture and SDK integration; independent review; package install and CI; publish executable prerelease with measured limitations. Test failure evidence precedes implementations where practical. No client compatibility is claimed beyond the actual SDK workflow recorded in VALIDATION.md.
