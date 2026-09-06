# Python integration roadmap

Bedrock inference, native Google ADK interoperability, and AgentCore application
request-boundary integration are locally tested with ADK 2.8.0 and AgentCore 1.22.0. Hosted AWS/ADOT and
backend mapping verification remain outstanding; see docs/integrations.md.
The remaining integrations below have no new priority relative to provider work.

## Required agent integrations

| Target | SDK work to assess and implement |
|---|---|
| Microsoft Agent Framework | Integrate with its native OTel instrumentation for agents, tools, and workflows. Test shared providers and framework-managed context. |
| Microsoft Foundry / Foundry Agent Service | Cover client-side agent interactions and document the separate platform configuration needed to export accessible hosted-agent telemetry. Foundry is the platform; Agent Framework is the application framework. |

For each integration, support requires a documented setup, supported dependency
and convention versions, runnable examples, lifecycle tests, correct parentage
and conversation association, and no duplicate model spans. Prefer native OTel;
do not rewrite third-party spans into our pinned convention version.

`init()` should configure everything available in the application process.
Cloud-service internal telemetry may need separate platform export configuration;
it cannot be exposed by an SDK running only in the caller. Backend interpretation
and ClickHouse mapping remain separate work.

References:
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-get-started.html)
- [Google ADK tracing](https://adk.dev/observability/traces/)
- [Microsoft Agent Framework observability](https://github.com/microsoft/agent-framework/blob/main/python/samples/02-agents/observability/README.md)
- [Microsoft Foundry tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-setup)

## Model providers and other candidates

- Bedrock Runtime: Boto3 `converse` and `converse_stream` are implemented. `invoke_model`, `invoke_model_with_response_stream`, native async
  clients are outside that support claim. AgentCore application integration is separate.
- Other candidates remain Pydantic AI, Strands, Azure OpenAI/Vertex AI deployment
  validation, and individually tested OpenAI-compatible endpoints.
- Existing provider gaps include embeddings and OpenAI's `responses.stream()` helper.

See the [release matrix](docs/compatibility.md) for implemented coverage.
