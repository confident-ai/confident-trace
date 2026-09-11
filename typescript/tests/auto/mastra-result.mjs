/* global ReadableStream */
import assert from 'node:assert/strict';
import { init, turn, traceContext } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const sink = new InMemorySpanExporter();
const runtime = init({ exporter: sink });
const { Mastra } = await import('@mastra/core');
const { Agent } = await import('@mastra/core/agent');
const model = {
  specificationVersion: 'v2',
  provider: 'mock',
  modelId: 'mock-model',
  supportedUrls: {},
  async doGenerate() {
    return {
      content: [{ type: 'text', text: 'Mastra answer' }],
      finishReason: 'stop',
      usage: { inputTokens: 2, outputTokens: 1, totalTokens: 3 },
      warnings: [],
    };
  },
  async doStream() {
    return {
      stream: new ReadableStream({
        start(controller) {
          for (const chunk of [
            { type: 'stream-start', warnings: [] },
            { type: 'text-start', id: '1' },
            { type: 'text-delta', id: '1', delta: 'Mastra answer' },
            { type: 'text-end', id: '1' },
            {
              type: 'finish',
              finishReason: 'stop',
              usage: { inputTokens: 2, outputTokens: 1, totalTokens: 3 },
            },
          ])
            controller.enqueue(chunk);
          controller.close();
        },
      }),
      warnings: [],
    };
  },
};
const assistant = new Agent({
  id: 'test',
  name: 'Test',
  instructions: 'Answer',
  model,
});
const mastra = new Mastra({ agents: { assistant }, logger: false });
let original;
const result = await turn(
  { name: 'mastra-result', threadId: 'test' },
  async () => {
    await mastra.getAgent('assistant').generate('First question');
    original = await mastra.getAgent('assistant').generate('Hi');
    return original;
  },
);
assert.equal(result, original);
assert.equal(result.text, 'Mastra answer');
await runtime.flush();
const root = sink.getFinishedSpans().find((s) => s.name === 'mastra-result');
assert.equal(
  root.attributes['confident.trace.output'],
  JSON.stringify('Mastra answer'),
);
assert.equal(root.attributes['confident.trace.name'], 'mastra-result');
const spans = sink.getFinishedSpans();
const llms = spans.filter((s) => s.attributes['confident.span.type'] === 'llm');
assert.equal(llms.length, 2);
assert.equal(new Set(spans.map((s) => s.spanContext().traceId)).size, 1);
assert.equal(spans.filter((s) => !s.parentSpanContext).length, 1);
for (const llm of llms) {
  assert.ok(llm.attributes['gen_ai.input.messages'], 'inference input missing');
}
assert.ok(
  JSON.stringify(
    llms.map((s) => s.attributes['gen_ai.input.messages']),
  ).includes('First question'),
);
assert.ok(
  JSON.stringify(
    llms.map((s) => s.attributes['gen_ai.input.messages']),
  ).includes('Hi'),
);
assert.ok(
  spans
    .filter((s) => s !== root)
    .every((s) => s.attributes['confident.trace.name'] === undefined),
  'native children must not rename the enclosing turn',
);
assert.equal(llms[0].attributes['mastra.span.type'], 'model_inference');
assert.equal(llms[0].attributes['gen_ai.operation.name'], 'chat');
assert.equal(llms[0].attributes['gen_ai.usage.input_tokens'], 2);
assert.equal(llms[0].attributes['gen_ai.usage.output_tokens'], 1);
for (const type of ['model_generation', 'model_step']) {
  const parent = spans.find((s) => s.attributes['mastra.span.type'] === type);
  assert.ok(parent, `missing ${type}`);
  assert.equal(parent.attributes['confident.span.type'], 'custom');
  assert.equal(parent.attributes['gen_ai.operation.name'], undefined);
  assert.equal(parent.attributes['gen_ai.usage.input_tokens'], undefined);
  assert.equal(parent.attributes['gen_ai.usage.output_tokens'], undefined);
}
assert.equal(
  spans.reduce(
    (sum, s) => sum + (s.attributes['gen_ai.usage.input_tokens'] ?? 0),
    0,
  ),
  4,
);
assert.equal(
  spans.reduce(
    (sum, s) => sum + (s.attributes['gen_ai.usage.output_tokens'] ?? 0),
    0,
  ),
  2,
);
// The other two enabled examples each add one standalone trace.
await mastra.getAgent('assistant').generate('Standalone');
await traceContext(
  { tags: ['support'], metadata: { release: '2026-09' }, userId: 'user-42' },
  () => mastra.getAgent('assistant').generate('Contextual'),
);
await runtime.flush();
assert.equal(
  new Set(sink.getFinishedSpans().map((s) => s.spanContext().traceId)).size,
  3,
);
const contextual = sink
  .getFinishedSpans()
  .filter((s) => s.attributes['confident.trace.tags']?.includes('support'));
assert.equal(
  contextual.length,
  1,
  'Only the contextual root inherits defaults',
);
assert.equal(contextual[0].attributes['confident.trace.user_id'], 'user-42');
assert.equal(contextual[0].parentSpanContext, undefined);
assert.equal(contextual[0].attributes['mastra.span.type'], 'agent_run');
assert.equal(
  sink.getFinishedSpans().filter((s) => !s.parentSpanContext).length,
  3,
);
for (const span of sink.getFinishedSpans().filter((s) => s !== contextual[0])) {
  assert.equal(span.attributes['confident.trace.user_id'], undefined);
  assert.ok(
    !String(span.attributes['confident.trace.metadata']).includes('2026-09'),
  );
}
assert.deepEqual(
  JSON.parse(contextual[0].attributes['confident.trace.metadata']),
  { release: '2026-09' },
);
await runtime.shutdown();
const disabledSink = new InMemorySpanExporter();
const disabledRuntime = init({ exporter: disabledSink, instrumentations: [] });
const disabledAgent = new Agent({
  id: 'disabled',
  name: 'Disabled',
  instructions: 'Answer',
  model,
});
const disabledMastra = new Mastra({ agents: { disabledAgent }, logger: false });
await disabledMastra.getAgent('disabledAgent').generate('No tracing');
await disabledRuntime.flush();
assert.equal(disabledSink.getFinishedSpans().length, 0);
await disabledRuntime.shutdown();
console.log('Mastra parent output passed');
