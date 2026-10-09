# Contributing

**Status: Planning — no executable release yet.** Contributions currently help refine the measurement contract, compatibility workflow, privacy defaults, and acceptance criteria. All implementation described in this repository is planned.

## Useful contributions now

- A reproducible problem where knowing actual call counts, response latency, categorized errors, or response bytes would help.
- Corrections to JSON-RPC/MCP assumptions, with the relevant protocol version and source.
- A proposal for the first supported client/version and a deterministic stdio workflow.
- Reviews of [MVP](docs/MVP.md), [ROADMAP](ROADMAP.md), and the English/Chinese README pair.

Please describe the observable fact separately from an inference. A schema count is not proof of model context, and response bytes are not a billing measurement. Historical client issues need a version-specific reproduction before they become claims about current behavior.

## Reporting a proposed workflow

Include the client/version, server/version, MCP protocol version, operating system, and Python version if known. Explain the task, expected tool behavior, actual symptom, and which metadata would resolve it. Label untested assumptions explicitly.

Prefer a synthetic fixture or a short sanitized reproduction. Do not publish real tool arguments, result bodies, credentials, environment values, private paths, raw stderr, or client configuration containing secrets. Tool names and request IDs may also need sanitizing. Obtain the owner's permission before sharing somebody else's session evidence.

## Changes and review

Keep scope aligned with one stdio workflow and local reports. Explain the problem, the proposed behavior, and its acceptance criteria before introducing another transport, client, dependency, or data-capture option. Discuss work through repository issues or pull requests; this document does not claim any have been created yet.

For documentation changes, check that links resolve, English and Chinese status/scope agree, and planned features remain labeled. Avoid invented installation commands, releases, benchmarks, test results, CI badges, or cost-saving claims.

For future implementation contributions, meaningful checks are expected for protocol forwarding, typed-ID correlation, failure boundaries, privacy defaults, and report consistency. State the exact checks actually run and their results. The current repository provides no test runner, executable CLI, or verified installation path.

## Privacy and dependencies

Default artifacts must remain metadata-only. A proposal to retain message bodies requires an explicit design review and is outside the current MVP. Keep temporary parsing separate from persisted output, and cover disclosure risks in any new output field.

Python 3.11+ with the standard library is the planned baseline. Propose a dependency only when it addresses a defined need, including maintenance and licensing implications. Do not add hosted collection or automatic client-configuration changes within this MVP.

## License

Contributions are intended to be distributed under this repository's MIT license; see [LICENSE](LICENSE).
