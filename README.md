# mcp-session-profiler

[简体中文](README.zh-CN.md)

**Status: Planning — no executable release yet.** This repository contains the proposed scope and acceptance criteria. The features below are planned, not implemented or verified.

A planned local profiler for one client's stdio MCP workflow. It aims to show which tools were called, how long matching responses took, which calls reported errors, and how large responses were on the observed transport.

## Planned workflow

1. A user manually places the profiler between one MCP client and one stdio server.
2. The planned observer forwards protocol traffic and retains metadata, without recording argument or result bodies by default.
3. Planned local JSON and HTML reports summarize tool-call frequency, response latency, error categories, and response bytes.
4. A user runs a task twice with explicitly chosen configurations, then creates a planned A/B comparison from the two reports.

The proposed implementation is a Python 3.11+ CLI, using the standard library where practical. The first client/version and supported MCP protocol version will be selected and documented through the compatibility milestone. Installation instructions and executable commands will be added only after an implementation exists.

## What the planned reports can establish

| Metric | Planned interpretation |
| --- | --- |
| Call frequency | Observed `tools/call` requests in the instrumented connection |
| Response latency | Local elapsed time from an observed request to its matched response; includes transport and observer effects |
| Errors | Distinct JSON-RPC errors, tool results marked `isError`, and transport/observation failures |
| Response size | UTF-8 bytes of the response message at the observed boundary; not model context tokens |
| A/B comparison | Differences between two user-labeled runs, with counts and limitations; not proof of causality |

Schema size, protocol traffic, token estimates, the model's actual context, and billing are different quantities. A stdio observer cannot establish what a client loaded into a model or what a provider charged. Any future token estimate must state its method and limitations. No precise cost or context savings are promised.

## Privacy and scope

The planned default is local metadata only: tool name, typed request ID, timing, byte counts, outcome category, and correlation diagnostics. Tool names and IDs can themselves be sensitive. Argument/result bodies and raw diagnostic text are not planned default artifacts. The observer must parse messages transiently to classify them and measure size.

The MVP will not automatically edit client configuration, disable tools, or send reports to a hosted service. Users will control configuration changes and any report sharing.

## Why this project

Static tool-schema counting is already covered by projects such as [mcp-tokens](https://github.com/sd2k/mcp-tokens) and [mcp-token-audit](https://github.com/michaeltuszynski/mcp-token-audit). This project's proposed focus is metadata from an actual session, followed by a manual comparison.

[Claude Code issue #29995](https://github.com/anthropics/claude-code/issues/29995) is a March 2026 user report that a diagnostic counted the full schemas of deferred tools. The research snapshot recorded it as **Closed as not planned**. That historical report does not establish whether a current client version has the same behavior; compatibility work must verify the selected version.

## Planning documents

- [MVP scope and measurement contract](docs/MVP.md)
- [Milestones and acceptance criteria](ROADMAP.md)
- [Contribution guidelines](CONTRIBUTING.md)

## License

MIT; see [LICENSE](LICENSE).
