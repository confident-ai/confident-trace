<img src="assets/confident-trace-banner-radial-4s.svg" alt="Confident Trace — illuminated paths across a star-filled landscape" width="100%" />

# `confident-trace`: Otel Native Tracing for AI Systems

Confident's OpenTelemetry-first tracing SDK for AI applications.

- [`python/`](python/): installable Python package, tests, examples, and benchmarks.
- [`spec/`](spec/): language-neutral versioned GenAI registry, release manifests, and shared fixtures.
- [`typescript/`](typescript/): TypeScript SDK with OpenAI, Anthropic, Google GenAI,
  Mastra, Vercel AI SDK, LangChain, LangGraph, and OpenAI Agents integrations.

For TypeScript setup and usage, see the [TypeScript quickstart](typescript/README.md).

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

See the [integration guide](python/docs/integrations.md) for AgentCore, Google ADK,
and Microsoft Agent Framework setup, and the
[roadmap](python/ROADMAP.md) for remaining validation and provider work.
