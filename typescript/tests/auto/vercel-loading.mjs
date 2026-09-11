import assert from 'node:assert/strict';
import { init, traceContext, turn } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const warnings = [];
console.warn = (message) => warnings.push(message);
if (process.env.BRIDGE_FIRST) await import('@ai-sdk/otel');
const sink = new InMemorySpanExporter();
const rt = init({ exporter: sink });
const { generateText } = await import('ai');
const { MockLanguageModelV3 } = await import('ai/test');
for (let i = 0; i < 2; i++) {
  const result = await traceContext(
    { metadata: { loading: 'regression' } },
    () =>
      generateText({
        model: new MockLanguageModelV3({
          doGenerate: {
            content: [{ type: 'text', text: 'Hello' }],
            finishReason: { unified: 'stop', raw: 'stop' },
            usage: { inputTokens: { total: 2 }, outputTokens: { total: 1 } },
            warnings: [],
          },
        }),
        prompt: 'Hi',
      }),
  );
  assert.equal(result.text, 'Hello');
  await rt.flush();
  const spans = sink.getFinishedSpans();
  assert.equal(
    spans.length,
    (i + 1) * 3,
    'one agent, step, and model span per call',
  );
  const roots = spans.filter((s) => !s.parentSpanContext);
  assert.equal(roots.length, i + 1);
  assert.equal(new Set(spans.map((s) => s.spanContext().traceId)).size, i + 1);
  for (const root of roots) {
    const children = spans.filter(
      (s) => s.spanContext().traceId === root.spanContext().traceId,
    );
    const step = children.find((s) => s.name === 'step 1');
    const model = children.find(
      (s) => s.attributes['confident.span.type'] === 'llm',
    );
    assert.equal(step.parentSpanContext.spanId, root.spanContext().spanId);
    assert.equal(model.parentSpanContext.spanId, step.spanContext().spanId);
    assert.ok(JSON.stringify(root.attributes).includes('regression'));
  }
}
// Returning the SDK object from a turn must preserve its identity while
// capturing its public text as the parent output (the docs' scenario 5).
let sdkResult;
const returned = await turn(
  { name: 'result-capture', threadId: 'test' },
  async () => {
    sdkResult = await generateText({
      model: new MockLanguageModelV3({
        doGenerate: {
          content: [{ type: 'text', text: 'Turn answer' }],
          finishReason: { unified: 'stop', raw: 'stop' },
          usage: { inputTokens: { total: 2 }, outputTokens: { total: 1 } },
          warnings: [],
        },
      }),
      prompt: 'Hi',
    });
    return sdkResult;
  },
);
assert.equal(returned, sdkResult);
assert.equal(returned.text, 'Turn answer');
await turn(
  { name: 'private-result', threadId: 'test', captureContent: false },
  () => returned,
);
await rt.flush();
const root = sink.getFinishedSpans().find((s) => s.name === 'result-capture');
assert.equal(
  root.attributes['confident.span.output'],
  JSON.stringify('Turn answer'),
);
assert.equal(
  root.attributes['confident.trace.output'],
  JSON.stringify('Turn answer'),
);
const privateRoot = sink
  .getFinishedSpans()
  .find((s) => s.name === 'private-result');
assert.equal(privateRoot.attributes['confident.span.output'], undefined);
assert.equal(privateRoot.attributes['confident.trace.output'], undefined);
assert.equal(
  rt.getInstrumentationStatus().integrations['vercel-ai'],
  'enabled',
);
assert.deepEqual(warnings, []);
await rt.shutdown();
console.log('Vercel lazy attachment passed');
