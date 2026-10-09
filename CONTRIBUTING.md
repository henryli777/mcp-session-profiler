# Contributing

Start with a reproducible symptom in one stdio MCP connection. Include client/server versions, negotiated protocol, OS/Python, expected behavior and observed metadata. Keep facts separate from inferences: response bytes do not establish model context or billing.

## Develop and validate

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
python examples/demo.py --output-dir /tmp/mcp-demo-new
```

Optional real SDK check:

```sh
python -m pip install 'mcp==2.3.0'
python examples/sdk_check.py --output /tmp/mcp-sdk-new.json
```

Use new output paths. Add meaningful tests for changed protocol, correlation, lifecycle, privacy or metrics behavior. State exact checks run. Keep runtime dependencies in the standard library unless a concrete need warrants a dependency. Keep English/Chinese status and scope aligned.

## Privacy and review

Use synthetic fixtures. Do not publish actual parameters, results, credentials, environment values, private paths or raw stderr. Tool names/IDs may need sanitizing too. Report artifacts must stay metadata-only. Do not add hosted telemetry, automatic client edits or message-body capture to this release.

Pull requests should explain the problem, final behavior, validation and material limits. New transports/clients need version-specific evidence and their own measurement contract. Historical issues do not prove current client behavior. Never invent benchmarks, compatibility, test results or savings.

Contributions are distributed under the repository's MIT [license](LICENSE).
