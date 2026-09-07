# Claude native tracing investigation

Status: experimental; no connected-trace guarantee. Checked 2026-09-07 with
Claude Agent SDK 0.2.152 and bundled Claude Code 2.1.259.

## Observations and boundary

Live diagnostic calls returned successfully. Some produced no native batch.
Another produced two `claude_code.llm_request` spans, each with its own trace ID
and an empty parent ID, without `claude_code.interaction`. The collector accepted
that batch with HTTP 200 and zero rejected spans. Different IDs alone do not
establish duplicate inference: these may represent distinct internal calls.

The diagnostic's ordered markers showed remote settings waiting, settings
settling, telemetry enabled, model dispatch, response start, then export success.
The enabled marker precedes trace-provider registration in the bundled source;
it does not prove readiness when the interaction was created. Initialization
racing with interaction creation is a hypothesis, not a confirmed root cause.

The local real-CLI regression uses a fake model URL and verifies exact native
parentage. That URL skips the production remote-settings path. Source inspection
found the remote-settings mock getter inactive in this distributed binary.
The production path has not been reproduced locally. No supported readiness
barrier was found in the public observability API. We are not patching the CLI,
bypassing managed settings, sleeping for guessed startup delays, or rewriting
trace IDs. Draining the query remains necessary but is not sufficient.

## Recommended configuration

Use `ClaudeAgentOptions(env={"CLAUDE_CODE_ENABLE_TELEMETRY": "0"})` and wrap the
query in `ct.span("claude.invocation")`. This sacrifices child model/tool spans
and child metrics/logs, while preserving Python tracing. The integration respects
the explicit disable switch. Choosing one telemetry method avoids introducing a
second model-span producer; disconnected native roots cannot be safely reconciled
by session or timestamps.

## Upstream issue draft (not submitted)

Title: Agent SDK native traces intermittently missing or detached from inbound W3C context

Environment: Python SDK 0.2.152, bundled CLI 2.1.259, macOS, API-key authentication,
OTLP HTTP/protobuf. No detailed-tracing endpoint configured.

Steps: run `python/examples/mega/check_claude_export.py` with valid provider and
collector credentials. It opens a Python span, supplies native telemetry options,
uses one tool-free query, drains the iterator through EOF, and receives native
OTLP locally before forwarding it once to the configured collector.

Expected: a native interaction parented to the supplied Python context, with
model requests below it and the same trace ID.

Observed: successful query with no batch on some runs; on another run, two model
roots with separate trace IDs and no interaction, accepted by the collector.
The fake-model regression passes but bypasses remote-settings startup. We suspect
initialization ordering; could you confirm whether trace-provider initialization
is awaited before interaction creation, and whether a supported readiness barrier
exists? A self-contained production-startup reproduction is still needed.

References:
- [Official observability configuration and W3C propagation](https://code.claude.com/docs/en/agent-sdk/observability)
- [Related telemetry initialization report, different environment](https://github.com/anthropics/claude-code/issues/46204)
