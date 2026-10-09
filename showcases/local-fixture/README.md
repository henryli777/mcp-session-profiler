# Local fixture showcase

Executed on 2026-10-09 with CPython 3.13.7 on macOS. This is a controlled local stdio workflow, not a production-server benchmark.

The demo initialized protocol `2025-11-25`, discovered three tools, then made four client tool calls:

| Tool | Result | Observed latency ms | Response frame bytes |
| --- | --- | ---: | ---: |
| `fast_echo` | success, Unicode preserved | 0.149 | 148 |
| `slow_echo` | success, fixture sleeps 30 ms | 32.203 | 144 |
| `failing_tool` | tool error | 0.308 | 158 |
| `unknown_tool` | JSON-RPC error -32602 | 0.188 | 96 |

All four matched; server exited 0; observation was complete. Raw server stderr (64 bytes) was discarded. Each latency has **one sample**; it demonstrates observation, not a latency guarantee or overhead measurement. Bytes are full response JSON frames excluding LF, not model tokens or billing.

![Executed offline report](report-preview.png)

Artifacts from the executed run:

- [Session metadata](session.json)
- [Offline HTML](report.html) — download/open locally; GitHub displays its source
- [CLI transcript](transcript.txt)

The fixture intentionally includes synthetic secret markers in arguments, results, errors, stderr, argv and environment values. They are excluded from the JSON/HTML artifacts. Tool names and request IDs are still metadata, and can be sensitive for real sessions.

## Reproduce

Install the project from the root README, then run with a **new** output directory:

```sh
python examples/demo.py --output-dir /tmp/mcp-fixture-new --include-rpc-error
```

Timing will vary. Counts, result categories, response-byte counts and Unicode checks are deterministic for this fixture. Inspect errors alongside sample counts and observation coverage. [Official SDK validation](../../docs/VALIDATION.md) is a separate executed workflow.
