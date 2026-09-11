import assert from 'node:assert/strict';
import { init, traceContext } from 'confident-trace';
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
assert.equal(
  rt.getInstrumentationStatus().integrations['vercel-ai'],
  'enabled',
);
assert.deepEqual(warnings, []);
await rt.shutdown();
console.log('Vercel lazy attachment passed');
