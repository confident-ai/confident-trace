import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { generateText, streamText, registerTelemetry } from 'ai';
import { MockLanguageModelV3, simulateReadableStream } from 'ai/test';
import { OpenTelemetry } from '@ai-sdk/otel';
import { createVercelAITracer } from 'confident-trace/vercel-ai';
import { ConfidentLangChainCallbackHandler } from 'confident-trace/langchain';
import { RunnableLambda } from '@langchain/core/runnables';
import { Annotation, StateGraph, START, END } from '@langchain/langgraph';
const sink = new InMemorySpanExporter();
const rt = init({ exporter: sink });
const usage = { inputTokens: { total: 2 }, outputTokens: { total: 1 } };
const response = {
  content: [{ type: 'text', text: 'Hello' }],
  finishReason: { unified: 'stop', raw: 'stop' },
  usage,
  warnings: [],
};
const model = () => new MockLanguageModelV3({ doGenerate: response });
const count = () =>
  sink
    .getFinishedSpans()
    .filter((s) => s.attributes['confident.span.type'] === 'llm').length;
let userCalls = 0;
await generateText({
  model: model(),
  prompt: 'Hi',
  telemetry: {
    integrations: [
      {
        onStart() {
          userCalls++;
        },
      },
    ],
  },
});
await rt.flush();
assert.equal(userCalls, 1);
assert.equal(count(), 1);
await generateText({
  model: model(),
  prompt: 'Hi',
  telemetry: { isEnabled: false },
});
await rt.flush();
assert.equal(count(), 1);
const manual = new OpenTelemetry({ tracer: createVercelAITracer() });
await generateText({
  model: model(),
  prompt: 'Hi',
  telemetry: { integrations: [manual] },
});
await rt.flush();
assert.equal(count(), 2, 'manual and automatic local telemetry duplicated');
const result = streamText({
  model: new MockLanguageModelV3({
    doStream: {
      stream: simulateReadableStream({
        initialDelayInMs: null,
        chunkDelayInMs: null,
        chunks: [
          { type: 'text-start', id: 't' },
          { type: 'text-delta', id: 't', delta: 'Hello' },
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
});
let text = '';
for await (const chunk of result.textStream) text += chunk;
assert.equal(text, 'Hello');
await rt.flush();
assert.equal(count(), 3);
registerTelemetry(manual);
await generateText({ model: model(), prompt: 'Hi' });
await rt.flush();
assert.equal(count(), 4, 'manual and automatic global telemetry duplicated');
const handler = new ConfidentLangChainCallbackHandler();
const chain = RunnableLambda.from((x) => x + '!');
assert.equal(
  await chain.invoke('hello', {
    callbacks: [
      handler,
      {
        name: 'user',
        handleChainStart() {
          userCalls++;
        },
      },
    ],
  }),
  'hello!',
);
await rt.flush();
assert.equal(
  sink.getFinishedSpans().filter((s) => s.name === 'RunnableLambda').length,
  1,
);
assert.equal(userCalls, 2);
const graph = new StateGraph(Annotation.Root({ value: Annotation() }))
  .addNode('answer', (s) => ({ value: s.value + '!' }))
  .addEdge(START, 'answer')
  .addEdge('answer', END)
  .compile();
await Promise.all(
  ['a', 'b'].map(async (value) => {
    const chunks = [];
    for await (const chunk of await graph.stream(
      { value },
      { streamMode: 'values' },
    ))
      chunks.push(chunk);
    assert.equal(chunks.at(-1).value, value + '!');
  }),
);
await rt.flush();
const roots = sink
  .getFinishedSpans()
  .filter((s) => s.name === 'LangGraph' && !s.parentSpanContext);
assert.equal(roots.length, 2);
assert.equal(new Set(roots.map((s) => s.spanContext().traceId)).size, 2);
handler.close();
await rt.shutdown();
console.log(
  'Framework streaming, concurrency, user callbacks, telemetry opt-out, and manual coexistence passed',
);
