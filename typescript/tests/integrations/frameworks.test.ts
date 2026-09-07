import {
  context,
  propagation,
  trace,
  SpanKind,
  SpanStatusCode,
} from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { generateText, streamText, tool, jsonSchema, stepCountIs } from 'ai';
import { MockLanguageModelV3, simulateReadableStream } from 'ai/test';
import { OpenTelemetry, LegacyOpenTelemetry } from '@ai-sdk/otel';
import { Observability } from '@mastra/observability';
import { SpanType } from '@mastra/core/observability';
import type { ObservabilityExporter } from '@mastra/core/observability';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createVercelAITracer } from '@/integrations/vercel-ai';
import { ConfidentMastraExporter } from '@/integrations/mastra';
import { ContentPolicy } from '@/content/policy';
import { state } from '@/runtime/state';

let exporter: InMemorySpanExporter;
let provider: NodeTracerProvider;
const close: (() => Promise<void>)[] = [];
beforeEach(() => {
  trace.disable();
  context.disable();
  propagation.disable();
  delete state.runtime;
  delete state.policy;
  exporter = new InMemorySpanExporter();
  provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  provider.register();
});
afterEach(async () => {
  for (const shutdown of close.splice(0)) await shutdown();
  await provider.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
const usage = {
  inputTokens: {
    total: 7,
    noCache: 7,
    cacheRead: undefined,
    cacheWrite: undefined,
  },
  outputTokens: { total: 3, text: 3, reasoning: undefined },
};
function model() {
  return new MockLanguageModelV3({
    provider: 'openai.chat',
    modelId: 'test-model',
    doGenerate: {
      content: [{ type: 'text', text: 'Hello' }],
      finishReason: { unified: 'stop', raw: 'stop' },
      usage,
      warnings: [],
      response: {
        id: 'response-1',
        modelId: 'response-model',
        timestamp: new Date(),
      },
    },
  });
}
const spans = () => exporter.getFinishedSpans();

it.each([OpenTelemetry, LegacyOpenTelemetry])(
  'uses real AI SDK generation with %s and preserves async parentage',
  async (Integration) => {
    const telemetry = {
      integrations: [new Integration({ tracer: createVercelAITracer() })],
    };
    await trace.getTracer('app').startActiveSpan('parent', async (parent) => {
      const result = await generateText({
        model: model(),
        prompt: 'Hi',
        telemetry,
      });
      expect(result.text).toBe('Hello');
      parent.end();
    });
    const owned = spans().filter(
      (s) => s.attributes['confident.span.integration'] === 'Vercel AI SDK',
    );
    expect(owned.length).toBeGreaterThanOrEqual(2);
    const client = owned.find((s) => s.kind === SpanKind.CLIENT)!;
    expect(
      owned.some((s) => s.attributes['confident.span.type'] === 'agent'),
    ).toBe(true);
    expect(
      owned.every(
        (s) => typeof s.attributes['confident.span.type'] === 'string',
      ),
    ).toBe(true);
    expect(client.attributes).toMatchObject({
      'confident.span.type': 'llm',
      'gen_ai.request.model': 'test-model',
      'gen_ai.response.model': 'response-model',
      'gen_ai.response.id': 'response-1',
      'gen_ai.usage.input_tokens': 7,
      'gen_ai.usage.output_tokens': 3,
      'gen_ai.response.finish_reasons': ['stop'],
    });
    expect(
      JSON.parse(String(client.attributes['gen_ai.output.messages']))[0]
        .parts[0],
    ).toEqual({ type: 'text', content: 'Hello' });
    expect(
      owned.every(
        (s) =>
          s.instrumentationScope.schemaUrl ===
          'https://opentelemetry.io/schemas/1.37.0',
      ),
    ).toBe(true);
    const parent = spans().find((s) => s.name === 'parent')!;
    expect(
      owned.some(
        (s) => s.parentSpanContext?.spanId === parent.spanContext().spanId,
      ),
    ).toBe(true);
    expect(
      owned.every(
        (s) => s.spanContext().traceId === parent.spanContext().traceId,
      ),
    ).toBe(true);
  },
);
it('retains streaming output, usage, SDK callbacks and response helpers', async () => {
  const onFinish = vi.fn();
  const m = new MockLanguageModelV3({
    provider: 'openai.chat',
    modelId: 'stream-model',
    doStream: {
      stream: simulateReadableStream({
        initialDelayInMs: null,
        chunkDelayInMs: null,
        chunks: [
          { type: 'text-start', id: 't' },
          { type: 'text-delta', id: 't', delta: 'Hel' },
          { type: 'text-delta', id: 't', delta: 'lo' },
          { type: 'text-end', id: 't' },
          {
            type: 'finish',
            finishReason: { unified: 'stop', raw: 'stop' },
            usage,
          },
        ],
      }),
    },
  });
  const result = streamText({
    model: m,
    prompt: 'Hi',
    telemetry: {
      integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
    },
    onFinish,
  });
  const chunks: string[] = [];
  for await (const chunk of result.textStream) chunks.push(chunk);
  expect(chunks.join('')).toBe('Hello');
  expect(await result.text).toBe('Hello');
  expect(onFinish).toHaveBeenCalledOnce();
  const client = spans().find((s) => s.kind === SpanKind.CLIENT)!;
  expect(client.attributes['gen_ai.usage.output_tokens']).toBe(3);
  expect(
    JSON.parse(String(client.attributes['gen_ai.output.messages']))[0].parts[0]
      .content,
  ).toBe('Hello');
});
it('records AI SDK multi-step tools with the tool span active inside execute', async () => {
  let parentId: string | undefined;
  const m = model();
  m.doGenerate = vi
    .fn()
    .mockResolvedValueOnce({
      content: [
        {
          type: 'tool-call',
          toolCallId: 'call-1',
          toolName: 'lookup',
          input: '{"city":"Macau"}',
        },
      ],
      finishReason: { unified: 'tool-calls', raw: 'tool_calls' },
      usage,
      warnings: [],
    })
    .mockResolvedValueOnce({
      content: [{ type: 'text', text: 'Done' }],
      finishReason: { unified: 'stop', raw: 'stop' },
      usage,
      warnings: [],
    });
  const result = await generateText({
    model: m,
    prompt: 'Weather?',
    stopWhen: stepCountIs(2),
    tools: {
      lookup: tool({
        inputSchema: jsonSchema<{ city: string }>({
          type: 'object',
          properties: { city: { type: 'string' } },
          required: ['city'],
        }),
        execute: async () => {
          parentId = trace.getSpan(context.active())?.spanContext().spanId;
          return { temperature: 25 };
        },
      }),
    },
    telemetry: {
      integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
    },
  });
  expect(result.text).toBe('Done');
  const toolSpan = spans().find(
    (s) => s.attributes['gen_ai.operation.name'] === 'execute_tool',
  )!;
  expect(toolSpan.kind).toBe(SpanKind.INTERNAL);
  expect(toolSpan.attributes['confident.span.type']).toBe('tool');
  expect(parentId).toBe(toolSpan.spanContext().spanId);
  expect(toolSpan.attributes['gen_ai.tool.name']).toBe('lookup');
  expect(
    JSON.parse(String(toolSpan.attributes['confident.span.input'])),
  ).toEqual({ city: 'Macau' });
  expect(
    JSON.parse(String(toolSpan.attributes['confident.span.output'])),
  ).toEqual({ temperature: 25 });
});
it('redacts AI SDK content and drops raw headers, metadata and error payloads', async () => {
  const tracer = createVercelAITracer({ redact: () => '[redacted]' });
  await generateText({
    model: model(),
    prompt: 'secret',
    headers: { authorization: 'secret' },
    telemetry: {
      integrations: [
        new OpenTelemetry({ tracer, headers: true, providerMetadata: true }),
      ],
    },
  });
  expect(
    spans().some(
      (s) => s.attributes['gen_ai.input.messages'] === '"[redacted]"',
    ),
  ).toBe(true);
  expect(JSON.stringify(spans().map((s) => s.attributes))).not.toContain(
    'secret',
  );
  const m = model();
  const error = new Error('secret');
  m.doGenerate = async () => {
    throw error;
  };
  await expect(
    generateText({
      model: m,
      prompt: 'secret',
      maxRetries: 0,
      telemetry: { integrations: [new OpenTelemetry({ tracer })] },
    }),
  ).rejects.toBe(error);
  expect(spans().some((s) => s.status.code === SpanStatusCode.ERROR)).toBe(
    true,
  );
  expect(
    JSON.stringify(spans().map((s) => [s.attributes, s.status, s.events])),
  ).not.toContain('secret');
});
it('honors runtime content opt-out and bounds upstream AI SDK JSON before parsing', async () => {
  state.policy = new ContentPolicy({ captureContent: false });
  await generateText({
    model: model(),
    prompt: 'secret',
    telemetry: {
      integrations: [
        new OpenTelemetry({
          tracer: createVercelAITracer({ maxContentBytes: 128 }),
        }),
      ],
    },
  });
  expect(
    spans().every(
      (s) =>
        !('gen_ai.input.messages' in s.attributes) &&
        !('gen_ai.output.messages' in s.attributes),
    ),
  ).toBe(true);
  delete state.policy;
  const tracer = createVercelAITracer({ maxContentBytes: 128 });
  const span = tracer.startSpan('chat huge');
  span.setAttribute(
    'gen_ai.output.messages',
    JSON.stringify([
      {
        role: 'assistant',
        parts: [{ type: 'text', content: 'x'.repeat(10000) }],
      },
    ]),
  );
  span.end();
  expect(spans().at(-1)!.attributes['gen_ai.output.messages']).toBe(
    '"[truncated]"',
  );
});
it('keeps simultaneous generations in separate trace trees with one shared integration', async () => {
  const telemetry = {
    integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
  };
  await Promise.all(
    ['a', 'b'].map((name) =>
      trace.getTracer('app').startActiveSpan(name, async (parent) => {
        await generateText({ model: model(), prompt: name, telemetry });
        parent.end();
      }),
    ),
  );
  for (const name of ['a', 'b']) {
    const root = spans().find((s) => s.name === name)!;
    expect(
      spans().filter(
        (s) => s.spanContext().traceId === root.spanContext().traceId,
      ).length,
    ).toBeGreaterThanOrEqual(3);
  }
});
function mastra(
  options: ConstructorParameters<typeof ConfidentMastraExporter>[0] = {},
) {
  const sink = new InMemorySpanExporter();
  const bridge = new ConfidentMastraExporter({ ...options, exporter: sink });
  const compatible: ObservabilityExporter = bridge;
  void compatible;
  const observability = new Observability({
    configs: { default: { serviceName: 'test', exporters: [bridge] } },
  });
  close.push(() => observability.shutdown());
  return {
    sink,
    bridge,
    observability,
    instance: observability.getInstance('default')!,
  };
}
it('exports real Mastra agent/model/tool trees with original IDs and usage', async () => {
  const { sink, observability, instance } = mastra();
  const agent = instance.startSpan({
    type: SpanType.AGENT_RUN,
    name: 'assistant',
    input: 'Hi',
  });
  const llm = agent.createChildSpan({
    type: SpanType.MODEL_GENERATION,
    name: 'generation',
    input: [{ role: 'user', content: 'Hi' }],
    attributes: { model: 'test-model', provider: 'openai' },
  });
  const toolSpan = agent.createChildSpan({
    type: SpanType.TOOL_CALL,
    name: 'lookup',
    input: { city: 'Macau' },
  });
  toolSpan.end({ output: { temperature: 25 } });
  llm.end({
    output: { text: 'Hello' },
    attributes: {
      usage: { inputTokens: 7, outputTokens: 3 },
      finishReason: 'stop',
      responseId: 'response-1',
      responseModel: 'test-model',
    },
  });
  agent.end({ output: 'Hello' });
  await observability.flush();
  const result = sink.getFinishedSpans();
  expect(result).toHaveLength(3);
  expect(
    result.find((s) => s.name === 'assistant')!.attributes[
      'confident.span.type'
    ],
  ).toBe('agent');
  expect(
    result.find((s) => s.name === 'lookup')!.attributes['confident.span.type'],
  ).toBe('tool');
  const child = result.find((s) => s.name === 'generation')!;
  expect(child.spanContext()).toMatchObject({
    spanId: llm.id,
    traceId: agent.traceId,
  });
  expect(child.parentSpanContext?.spanId).toBe(agent.id);
  expect(child.attributes).toMatchObject({
    'confident.span.integration': 'Mastra',
    'confident.span.type': 'llm',
    'gen_ai.usage.input_tokens': 7,
    'gen_ai.usage.output_tokens': 3,
    'gen_ai.response.finish_reasons': ['stop'],
  });
  expect(
    JSON.parse(String(child.attributes['gen_ai.output.messages']))[0].parts[0]
      .content,
  ).toBe('Hello');
  expect(
    result.find((s) => s.name === 'lookup')!.attributes[
      'gen_ai.operation.name'
    ],
  ).toBe('execute_tool');
  expect(
    result.find((s) => s.name === 'assistant')!.attributes[
      'confident.trace.output'
    ],
  ).toBe(
    JSON.stringify([
      { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
    ]),
  );
});
it('exports resumed Mastra spans without needing prior start events', async () => {
  const { bridge, sink } = mastra();
  const source = {
    id: '1234567890abcdef',
    traceId: '1234567890abcdef1234567890abcdef',
    parentSpanId: 'abcdef1234567890',
    name: 'resumed-step',
    type: 'workflow_step',
    startTime: new Date(1000),
    endTime: new Date(1500),
    isRootSpan: false,
    isEvent: false,
    input: { value: 1 },
    output: { value: 2 },
  };
  await bridge.exportTracingEvent({
    type: 'span_started',
    exportedSpan: source,
  });
  await bridge.exportTracingEvent({
    type: 'span_updated',
    exportedSpan: source,
  });
  await bridge.flush();
  expect(sink.getFinishedSpans()).toHaveLength(0);
  await bridge.exportTracingEvent({ type: 'span_ended', exportedSpan: source });
  await bridge.flush();
  const span = sink.getFinishedSpans()[0]!;
  expect(span.attributes['confident.span.type']).toBe('custom');
  expect(span.startTime).toEqual([1, 0]);
  expect(span.endTime).toEqual([1, 500000000]);
  expect(span.duration).toEqual([0, 500000000]);
  expect(span.parentSpanContext?.spanId).toBe(source.parentSpanId);
});
it('applies Mastra privacy and preserves failure status without exception text', async () => {
  const { sink, observability, instance } = mastra({ captureContent: false });
  const span = instance.startSpan({
    type: SpanType.WORKFLOW_RUN,
    name: 'workflow',
    input: 'secret',
    metadata: { key: 'secret' },
  });
  span.error({ error: new Error('secret'), endSpan: true });
  await observability.flush();
  const ended = sink.getFinishedSpans()[0]!;
  expect(ended.status.code).toBe(SpanStatusCode.ERROR);
  expect(ended.attributes['confident.span.type']).toBe('custom');
  expect(
    JSON.stringify([ended.attributes, ended.status, ended.events]),
  ).not.toContain('secret');
});
it('Mastra shutdown is idempotent and does not shut down the application OTel provider', async () => {
  const { bridge } = mastra();
  const first = bridge.shutdown();
  expect(bridge.shutdown()).toBe(first);
  await first;
  trace.getTracer('app').startSpan('still-active').end();
  expect(spans()).toHaveLength(1);
});

it('omits multimodal payloads and keeps existing third-party spans unchanged', () => {
  const tracer = createVercelAITracer();
  const span = tracer.startSpan('chat test');
  span.setAttributes({
    'gen_ai.input.messages': JSON.stringify([
      {
        role: 'user',
        parts: [
          { type: 'blob', content: 'secret-image-base64' },
          { type: 'text', content: 'Hello' },
        ],
      },
    ]),
    'gen_ai.system_instructions': JSON.stringify([
      { type: 'blob', content: 'secret-image-base64' },
    ]),
  });
  span.end();
  expect(JSON.stringify(spans()[0]!.attributes)).not.toContain(
    'secret-image-base64',
  );
  const other = trace.getTracer('third-party').startSpan('other');
  other.setAttribute('ai.prompt', 'unchanged');
  other.end();
  expect(spans()[1]!.attributes).toEqual({ 'ai.prompt': 'unchanged' });
});
it('ends AI SDK streams on an SDK abort without changing callback behavior', async () => {
  const controller = new AbortController();
  const onAbort = vi.fn();
  const result = streamText({
    model: new MockLanguageModelV3({
      doStream: {
        stream: simulateReadableStream({
          initialDelayInMs: null,
          chunkDelayInMs: 10,
          chunks: [
            { type: 'text-start', id: 't' },
            { type: 'text-delta', id: 't', delta: 'Hello' },
            { type: 'text-delta', id: 't', delta: ' there' },
            { type: 'text-end', id: 't' },
            {
              type: 'finish',
              finishReason: { unified: 'stop', raw: 'stop' },
              usage,
            },
          ],
        }),
      },
    }),
    prompt: 'Hi',
    abortSignal: controller.signal,
    onAbort,
    telemetry: {
      integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
    },
  });
  for await (const chunk of result.textStream) {
    void chunk;
    controller.abort();
  }
  expect(onAbort).toHaveBeenCalledOnce();
  expect(spans().length).toBeGreaterThan(0);
  expect(spans().every((span) => span.ended)).toBe(true);
});
it('respects disabled tracing in both framework integrations', async () => {
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  await generateText({
    model: model(),
    prompt: 'Hi',
    telemetry: {
      integrations: [new OpenTelemetry({ tracer: createVercelAITracer() })],
    },
  });
  expect(spans()).toHaveLength(0);
  const { sink, observability, instance } = mastra();
  const root = instance.startSpan({
    type: SpanType.AGENT_RUN,
    name: 'disabled',
  });
  root.end();
  await observability.flush();
  expect(sink.getFinishedSpans()).toHaveLength(0);
});
it('respects Mastra sampling, redaction, events and exporter resource precedence', async () => {
  const { sink, bridge, observability, instance } = mastra({
    redact: () => '[redacted]',
    resourceAttributes: { 'service.name': 'explicit' },
  });
  bridge.init({ config: { serviceName: 'mastra-service' } });
  const root = instance.startSpan({
    type: SpanType.WORKFLOW_RUN,
    name: 'workflow',
    input: 'secret',
  });
  root.createEventSpan({
    type: SpanType.GENERIC,
    name: 'event',
    output: 'secret',
  });
  root.end({ output: 'secret' });
  await observability.flush();
  expect(sink.getFinishedSpans()).toHaveLength(2);
  expect(
    sink.getFinishedSpans().find((s) => s.name === 'event')!.attributes[
      'confident.span.type'
    ],
  ).toBe('custom');
  expect(
    sink
      .getFinishedSpans()
      .every((s) => s.resource.attributes['service.name'] === 'explicit'),
  ).toBe(true);
  expect(
    JSON.stringify(sink.getFinishedSpans().map((s) => s.attributes)),
  ).not.toContain('secret');
  expect(
    sink.getFinishedSpans().find((s) => s.name === 'event')!.duration,
  ).toEqual([0, 0]);
  const { SamplingStrategyType } = await import('@mastra/observability');
  const never = new Observability({
    configs: {
      default: {
        serviceName: 'never',
        sampling: { type: SamplingStrategyType.NEVER },
        exporters: [bridge],
      },
    },
  });
  const skipped = never
    .getInstance('default')!
    .startSpan({ type: SpanType.AGENT_RUN, name: 'skipped' });
  skipped.end();
  await never.flush();
  expect(sink.getFinishedSpans()).toHaveLength(2);
  expect(
    sink.getFinishedSpans().find((s) => s.name === 'event')!.attributes[
      'confident.span.type'
    ],
  ).toBe('custom');
});
