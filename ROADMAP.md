# Roadmap

**Status: Planning — no executable release yet.** Every item is planned. Milestones are acceptance gates, not delivery-date commitments. Passing one gate does not imply later capabilities exist.

## Milestone 1 — Establish one observable stdio workflow

Planned work:

- Select one client/version, one MCP protocol version, and one documented local server fixture.
- Define the metadata contract, framing limits, subprocess lifecycle, and privacy defaults described in [MVP](docs/MVP.md).
- Implement a planned bidirectional observer and JSON-RPC correlation before report presentation.

Acceptance criteria:

- Publish the exact client, server, protocol, operating system, and Python versions used for the compatibility check.
- Demonstrate initialization and deterministic tool calls passing through the observer with unchanged protocol bytes.
- Confirm fixture call counts, response byte counts, outcome categories, and monotonic elapsed-time ordering against known inputs.
- Demonstrate requests completing out of order, notifications, typed IDs, orphan responses, duplicate IDs, unsupported batches, malformed messages, and child-process exit behavior without invented matches.
- Inspect default artifacts to confirm they contain no argument/result bodies or raw stderr text. Document remaining sensitive metadata and observer overhead measured in the fixture.

## Milestone 2 — Produce inspectable local reports

Planned work:

- Add a versioned JSON report format and an offline HTML report generated from it.
- Summarize tool frequency, matched-call latency, errors, response sizes, unresolved calls, and observation failures.
- Add limits for transient parsing, buffers, and retained metadata so long sessions have explicit bounds.

Acceptance criteria:

- Produce JSON and HTML from the same fixture, with totals and per-tool summaries agreeing with the input events.
- Distinguish matched-call statistics from incomplete or ambiguous calls; every displayed latency summary includes its sample count.
- Escape untrusted tool names and labels; verify HTML renders without external assets or network requests.
- Confirm empty sessions, large responses, invalid frames, and interrupted sessions produce explicit status rather than a misleading success report.
- Validate report schema/version handling and document retention, output location, and cleanup behavior.

## Milestone 3 — Validate manual A/B comparison and readiness

Planned work:

- Add local comparison of two user-selected session reports, with task/configuration labels supplied by the user.
- Document a reproducible task, known limitations, and the first supported workflow.
- Evaluate whether the observer helps real users resolve an actual slow call, large response, or error pattern.

Acceptance criteria:

- Compare fixture reports with known count, latency, error, and byte differences; reproduce the expected deltas.
- Report incompatible schema/protocol versions, missing labels, unequal call counts, and insufficient latency samples visibly. A one-run-per-condition comparison carries no statistical or causal claim.
- Complete and record at least three independent user trials where a report identifies a specific issue and the user confirms a useful diagnosis. Obtain permission before sharing sanitized evidence.
- Publish verified installation and usage instructions only for the implemented workflow, together with the executed checks and their results.
- Review scope and limitations before declaring an executable release; do not promote planned or estimated metrics to observed facts.

## Deferred decisions

Additional clients, HTTP transports, optional token estimators, and team baselines remain future candidates. They require new measurement contracts and evidence of need. Hosted collection, configuration automation, and exact billing attribution are outside the MVP.
