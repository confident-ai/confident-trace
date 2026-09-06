# confident-trace

Confident's OpenTelemetry-first tracing SDK for AI applications.

- [`python/`](python/): installable Python package, tests, examples, and benchmarks.
- [`spec/`](spec/): language-neutral versioned GenAI registry, release manifests, and shared fixtures.
- `typescript/`: intentionally empty, reserved for the TypeScript SDK.

Git does not track empty directories. Run `mkdir -p typescript` after cloning.

```sh
pip install -e './python[test]'
pytest python/tests
```

Start with the [Python quickstart](python/README.md) and
[support mechanisms and release compatibility matrix](python/docs/compatibility.md).

Registry generation: `python tools/generate_genai.py`; verify with `--check`.
See [registry maintenance](spec/genai/README.md) for upstream provenance and release policy.

## License

Licensed under the [Apache License 2.0](LICENSE). The root `LICENSE` is the source
of truth; `python/LICENSE` is an identical copy included in Python distributions.
When updating the license, refresh that copy; CI checks that both files match.

See the [integration roadmap](python/ROADMAP.md) for required AgentCore, Google ADK,
Microsoft Agent Framework, and Microsoft Foundry work.
