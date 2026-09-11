import assert from 'node:assert/strict';
import { init, turn } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const sink = new InMemorySpanExporter();
const runtime = init({ exporter: sink });
const { FakeListChatModel } = await import('@langchain/core/utils/testing');
const model = new FakeListChatModel({ responses: ['Details', 'Summary'] });
let message;
const result = await turn(
  { name: 'support-turn', threadId: 'chat-42' },
  async () => {
    await model.invoke('Find details');
    message = await model.invoke('Summarize details');
    return message;
  },
);
assert.equal(result, message);
assert.equal(result.content, 'Summary');
await runtime.flush();
const spans = sink.getFinishedSpans();
assert.equal(spans.length, 3);
const root = spans.find((s) => s.name === 'support-turn');
assert.equal(
  root.attributes['confident.span.output'],
  JSON.stringify('Summary'),
);
assert.equal(
  root.attributes['confident.trace.output'],
  JSON.stringify('Summary'),
);
assert.ok(
  spans
    .filter((s) => s !== root)
    .every((s) => s.parentSpanContext.spanId === root.spanContext().spanId),
);
const { RunnableLambda } = await import('@langchain/core/runnables');
await RunnableLambda.from((input) => input)
  .pipe(model)
  .invoke('Check chain name');
await runtime.flush();
const chainRoot = sink
  .getFinishedSpans()
  .find((s) => !s.parentSpanContext && s.name !== 'support-turn');
assert.equal(chainRoot.attributes['confident.trace.name'], chainRoot.name);
assert.notEqual(
  chainRoot.attributes['confident.trace.name'],
  'FakeListChatModel',
);
await runtime.shutdown();
console.log('LangChain returned message output passed');
