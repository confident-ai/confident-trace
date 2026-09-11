# Confident Trace Skills

[![skills.sh](https://skills.sh/b/confident-ai/confident-trace)](https://skills.sh/confident-ai/confident-trace)

Agent Skills that teach coding assistants how to instrument AI applications
with the `confident-trace` Python and TypeScript SDKs.

## Skills

| Skill                                    | Description                                                                                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| [confident-tracing](./confident-tracing) | Instrument an AI application with the Python or TypeScript confident-trace SDK. Direct counterpart to DeepEval's `deepeval-tracing` skill.        |
| [confident-otel](./confident-otel)       | Export raw, language-neutral OpenTelemetry AI traces without the confident-trace package. Direct counterpart to DeepEval's `deepeval-otel` skill. |

## Installation

### For Claude.ai (Web)

1. Download the `skills/confident-tracing` or `skills/confident-otel` folder
   from this repository.
2. Zip the folder.
3. In Claude.ai, navigate to **Settings > Capabilities > Skills**.
4. Click **Upload skill** and select your zipped folder.

### For Claude Code (Local CLI)

Install through the plugin marketplace manifest in this repository:

```
/plugin marketplace add confident-ai/confident-trace
/plugin install confident-trace@confident-trace-plugins
```

Or copy a skill folder directly into your local project's skills directory:

```bash
mkdir -p .claude/skills/
cp -r path/to/downloaded/confident-tracing .claude/skills/
```

### Cursor Plugin

This repository includes a Cursor plugin manifest that points to `./skills/`.
When installed as a plugin, Cursor discovers the `confident-tracing` and
`confident-otel` skills directly.

### Codex Plugin

This repository includes a Codex plugin manifest at
`.codex-plugin/plugin.json` that points to `./skills/`, so Codex discovers
both skills when the plugin is installed. Alternatively, copy a skill folder
into `.agents/skills/` in your project (or `~/.agents/skills/` for all
projects).

### skills CLI

Install either skill with a skills-compatible installer:

```bash
npx skills add confident-ai/confident-trace --skill "confident-tracing"
npx skills add confident-ai/confident-trace --skill "confident-otel"
```

### Manual Copy

Copy or symlink `skills/confident-tracing` or `skills/confident-otel` into
your agent's skills directory.

## Authentication

Set the project-scoped Confident AI API key before running the application:

```bash
export CONFIDENT_API_KEY="<your Confident AI project API key>"
```

With the confident-trace SDK (`confident-tracing` skill), use
`CONFIDENT_OTEL_ENDPOINT` only for EU, self-hosted, or collector exports; the
value is the complete traces endpoint. With raw OpenTelemetry
(`confident-otel` skill), configure the exporter endpoint directly as described
in that skill.
