# Roadmap

v0.1 is an executable alpha. Shipping this version establishes a local workflow; it does not establish broad client compatibility, a performance guarantee, or independently validated user benefit.

## Implemented in v0.1

- Standard-library Python CLI and bounded POSIX stdio proxy.
- Typed/origin ID correlation, matched-call latency, response bytes and categorized errors.
- Local metadata JSON and escaped offline HTML reports.
- Explicit incomplete/ambiguous/limited observation and bounded shutdown.
- Deterministic local fixture, privacy sentinels and official Python SDK compatibility check.
- Meaningful protocol/lifecycle/report tests and Linux/macOS CI.

Actual executed compatibility and its limits are recorded in [docs/VALIDATION.md](docs/VALIDATION.md); the local report is in [showcases/local-fixture](showcases/local-fixture/README.md).

## Next: validate usefulness and overhead

- Obtain three independent, permissioned user trials identifying a concrete slow tool, large response or error pattern. No trials are claimed yet.
- Reproduce a chosen desktop client's launch/shutdown workflow and record exact versions before claiming support.
- Publish direct-versus-proxy overhead measurements for a fixed fixture and environment; avoid universal timing guarantees.
- Improve repeated-session configuration ergonomics without overwriting or exposing private reports.

## Later candidates

- Descriptive manual A/B report comparison: labels, sample counts, incompatible inputs and undefined ratios visible; no causal claim.
- HTTP transport under a separate framing/lifecycle/measurement contract.
- Windows supervision under a tested process-tree contract.

Token estimates would require an explicit estimator and uncertainty. Hosted collection, exact billing attribution, automatic tool disabling and model-context reconstruction are outside current scope.
