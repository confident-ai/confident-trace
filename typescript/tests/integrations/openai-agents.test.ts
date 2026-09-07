import {
  context,
  propagation,
  trace,
  SpanStatusCode,
} from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import {
  Agent,
  Runner,
  Usage,
  setTraceProcessors,
  setTracingDisabled,
  withTrace,
  withAgentSpan,
  withGenerationSpan,
  withFunctionSpan,
  withHandoffSpan,
  withGuardrailSpan,
  withResponseSpan,
  withCustomSpan,
} from '@openai/agents';
import type { Model, ModelResponse, TracingProcessor } from '@openai/agents';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ConfidentOpenAIAgentsProcessor } from '@/integrations/openai-agents';
import { state } from '@/runtime/state';
import { ContentPolicy } from '@/content/policy';
let exporter: InMemorySpanExporter;
let provider: NodeTracerProvider;
let processor: ConfidentOpenAIAgentsProcessor;
beforeEach(() => {
  trace.disable();
  context.disable();
  propagation.disable();
  delete state.runtime;
  delete state.policy;
  setTracingDisabled(false);
  exporter = new InMemorySpanExporter();
  provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  provider.register();
  processor = new ConfidentOpenAIAgentsProcessor({
    flush: () => provider.forceFlush(),
  });
  const compatible: TracingProcessor = processor;
  setTraceProcessors([compatible]);
});
afterEach(async () => {
  await processor.shutdown();
  setTraceProcessors([]);
  await provider.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
const spans = () => exporter.getFinishedSpans();
it('exports real native agent/model/tool/handoff/guardrail spans under an OTel parent', async () => {
  await trace.getTracer('app').startActiveSpan('parent', async (parent) => {
    await withTrace('workflow', async () =>
      withAgentSpan(
        async () => {
          await withGenerationSpan(
            async (span) => {
              span.spanData.output = [{ role: 'assistant', content: 'Hello' }];
              span.spanData.usage = { input_tokens: 7, output_tokens: 3 };
            },
            {
              data: {
                model: 'test-model',
                input: [{ role: 'user', content: 'Hi' }],
              },
            },
          );
          await withFunctionSpan(
            async (span) => {
              span.spanData.output = 'done';
            },
            { data: { name: 'lookup', input: 'Macau' } },
          );
          await withHandoffSpan(async () => {}, {
            data: { from_agent: 'a', to_agent: 'b' },
          });
          await withGuardrailSpan(async () => {}, {
            data: { name: 'check', triggered: false },
          });
        },
        { data: { name: 'assistant' } },
      ),
    );
    parent.end();
  });
  await processor.forceFlush();
  const result = spans();
  expect(result).toHaveLength(7);
  const parent = result.find((s) => s.name === 'parent')!;
  const workflow = result.find((s) => s.name === 'workflow')!;
  const agent = result.find((s) => s.name === 'assistant')!;
  const llm = result.find((s) => s.name === 'generation')!;
  expect(
    result.every(
      (s) => s.spanContext().traceId === parent.spanContext().traceId,
    ),
  ).toBe(true);
  expect(workflow.parentSpanContext?.spanId).toBe(parent.spanContext().spanId);
  expect(agent.parentSpanContext?.spanId).toBe(workflow.spanContext().spanId);
  expect(llm.parentSpanContext?.spanId).toBe(agent.spanContext().spanId);
  expect(llm.attributes).toMatchObject({
    'confident.span.integration': 'OpenAI Agents SDK',
    'confident.span.type': 'llm',
    'gen_ai.request.model': 'test-model',
    'gen_ai.usage.input_tokens': 7,
    'gen_ai.usage.output_tokens': 3,
  });
  expect(String(llm.attributes['gen_ai.output.messages'])).toContain('Hello');
  expect(
    result.find((s) => s.name === 'lookup')!.attributes['confident.span.type'],
  ).toBe('tool');
  expect(
    result.find((s) => s.name === 'handoff')!.attributes['confident.span.type'],
  ).toBe('custom');
});
it('supports the real Runner with a local model in invoke and streaming modes', async () => {
  const response: ModelResponse = {
    usage: new Usage({ inputTokens: 7, outputTokens: 3, totalTokens: 10 }),
    output: [
      {
        type: 'message',
        role: 'assistant',
        status: 'completed',
        content: [{ type: 'output_text', text: 'Hello' }],
      },
    ],
  };
  const model: Model = {
    async getResponse() {
      return response;
    },
    async *getStreamedResponse() {
      yield {
        type: 'response_done',
        response: {
          id: 'r',
          usage: response.usage,
          output: [
            {
              type: 'message',
              role: 'assistant',
              status: 'completed',
              content: [{ type: 'output_text', text: 'Hello' }],
            },
          ],
        },
      };
    },
  };
  const runner = new Runner();
  const agent = new Agent({ name: 'assistant', model });
  const result = await runner.run(agent, 'Hi');
  expect(result.finalOutput).toBe('Hello');
  const streamed = await runner.run(agent, 'Hi', { stream: true });
  for await (const event of streamed) void event;
  await streamed.completed;
  expect(streamed.finalOutput).toBe('Hello');
  await processor.forceFlush();
  expect(
    spans().filter((s) => s.attributes['confident.span.type'] === 'agent'),
  ).toHaveLength(2);
  expect(new Set(spans().map((s) => s.spanContext().traceId)).size).toBe(2);
});
it('normalizes Responses text/tool calls and drops audio, metadata and error text', async () => {
  await withTrace('test', async () =>
    withResponseSpan(async (span) => {
      span.spanData._input = [
        {
          role: 'user',
          content: [
            { type: 'input_text', text: 'Hi' },
            { type: 'input_image', image_url: 'secret-image' },
          ],
        },
      ];
      span.spanData._response = {
        id: 'response-1',
        model: 'test-model',
        usage: { input_tokens: 7, output_tokens: 3 },
        output: [
          {
            role: 'assistant',
            content: [{ type: 'output_text', text: 'Hello' }],
          },
          {
            type: 'function_call',
            name: 'lookup',
            call_id: 'c',
            arguments: '{}',
          },
        ],
      };
      span.setError({
        message: 'secret-error',
        data: { apiKey: 'secret-key' },
      });
    }),
  );
  const result = spans().find((s) => s.name === 'response')!;
  expect(result.status.code).toBe(SpanStatusCode.ERROR);
  expect(result.attributes['gen_ai.response.id']).toBe('response-1');
  expect(String(result.attributes['gen_ai.output.messages'])).toContain(
    'tool_call',
  );
  expect(
    JSON.stringify(spans().map((s) => [s.attributes, s.events, s.status])),
  ).not.toContain('secret');
});
it('honors opt-out, redaction, payload bounds and disabled tracing', async () => {
  state.policy = new ContentPolicy({ captureContent: false });
  await withTrace('test', async () =>
    withCustomSpan(async () => {}, {
      data: { name: 'custom', data: { secret: 'secret' } },
    }),
  );
  expect(JSON.stringify(spans().map((s) => s.attributes))).not.toContain(
    'secret',
  );
  delete state.policy;
  await processor.shutdown();
  processor = new ConfidentOpenAIAgentsProcessor({
    redact: () => '[redacted]',
  });
  setTraceProcessors([processor]);
  await withTrace('test', async () =>
    withGenerationSpan(async () => {}, {
      data: { input: [{ role: 'user', content: 'secret' }] },
    }),
  );
  expect(
    spans().find((s) => s.name === 'generation')!.attributes[
      'gen_ai.input.messages'
    ],
  ).toBe('"[redacted]"');
  const count = spans().length;
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  await withTrace('disabled', async () =>
    withAgentSpan(async () => {}, { data: { name: 'ignored' } }),
  );
  expect(spans()).toHaveLength(count);
});
it('closes abandoned spans once, flushes failures explicitly, and leaves OTel running', async () => {
  await processor.onTraceStart({ traceId: 'abandoned', name: 'abandoned' });
  const first = processor.shutdown();
  expect(processor.shutdown()).toBe(first);
  await first;
  expect(spans()[0]!.status.code).toBe(SpanStatusCode.ERROR);
  trace.getTracer('app').startSpan('still-active').end();
  expect(spans()).toHaveLength(2);
  const failed = new ConfidentOpenAIAgentsProcessor({
    flush: async () => {
      throw new Error('failed');
    },
  });
  await expect(failed.forceFlush()).rejects.toThrow('failed');
  await expect(failed.shutdown()).rejects.toThrow('failed');
});

it('isolates parallel trace trees and bounds content on completed native spans', async () => {
  await processor.shutdown();
  processor = new ConfidentOpenAIAgentsProcessor({ maxContentBytes: 128 });
  setTraceProcessors([processor]);
  await Promise.all(
    ['a', 'b'].map((name) =>
      withTrace(name, async () =>
        withGenerationSpan(
          async (span) => {
            await Promise.resolve();
            span.spanData.output = [
              { role: 'assistant', content: 'x'.repeat(10000) },
            ];
          },
          { data: { model: name } },
        ),
      ),
    ),
  );
  const models = spans().filter(
    (s) => s.attributes['confident.span.type'] === 'llm',
  );
  expect(models).toHaveLength(2);
  expect(new Set(models.map((s) => s.spanContext().traceId)).size).toBe(2);
  expect(
    models.every(
      (s) => s.attributes['gen_ai.output.messages'] === '"[truncated]"',
    ),
  ).toBe(true);
  for (const model of models) {
    const root = spans().find(
      (s) => s.name === model.attributes['gen_ai.request.model'],
    )!;
    expect(model.parentSpanContext?.spanId).toBe(root.spanContext().spanId);
  }
});
