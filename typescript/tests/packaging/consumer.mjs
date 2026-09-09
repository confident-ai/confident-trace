import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { context, trace } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';

const originalProvider = trace.getTracerProvider();
const originalContext = context.active();
const listeners = ['beforeExit', 'exit', 'SIGTERM', 'SIGINT'].map((event) =>
  process.listenerCount(event),
);
const esm = await import('confident-trace');
const require = createRequire(import.meta.url);
const cjs = require('confident-trace');
assert.equal(trace.getTracerProvider(), originalProvider);
assert.equal(context.active(), originalContext);
assert.deepEqual(
  ['beforeExit', 'exit', 'SIGTERM', 'SIGINT'].map((event) =>
    process.listenerCount(event),
  ),
  listeners,
);
assert.equal(esm.init, cjs.init);
assert.deepEqual(Object.keys(esm).sort(), [
  'createSpanProcessor',
  'flush',
  'init',
  'shutdown',
  'span',
  'turn',
  'updateLlmSpan',
  'updateSpan',
  'updateTrace',
  'withProject',
  'withSpan',
  'withTracingSuppressed',
]);
const conventions = await import('confident-trace/semconv');
assert.equal(
  conventions.SCHEMA_URL,
  require('confident-trace/semconv').SCHEMA_URL,
);
assert.throws(() => require('confident-trace/runtime/init'), {
  code: 'ERR_PACKAGE_PATH_NOT_EXPORTED',
});
for (const [entry, name] of [
  ['openai', 'instrumentOpenAI'],
  ['anthropic', 'instrumentAnthropic'],
  ['google-genai', 'instrumentGoogleGenAI'],
  ['vercel-ai', 'createVercelAITracer'],
  ['mastra', 'ConfidentMastraExporter'],
  ['langchain', 'ConfidentLangChainCallbackHandler'],
  ['langgraph', 'ConfidentLangGraphCallbackHandler'],
  ['openai-agents', 'ConfidentOpenAIAgentsProcessor'],
]) {
  assert.equal(
    (await import(`confident-trace/${entry}`))[name],
    require(`confident-trace/${entry}`)[name],
  );
}
const exporter = new InMemorySpanExporter();
const runtime = esm.init({ exporter, captureContent: false });
assert.equal(cjs.init(), runtime);
assert.equal(runtime.active, true);
runtime.getTracer().startSpan('packed-consumer').end();
assert.equal(await cjs.flush(), true);
assert.equal(exporter.getFinishedSpans().length, 1);
assert.equal(
  exporter.getFinishedSpans()[0].instrumentationScope.version,
  process.env.EXPECTED_PACKAGE_VERSION,
);
const { instrumentOpenAI } = await import('confident-trace/openai');
const client = {
  chat: {
    completions: {
      create: async () => ({
        model: 'test',
        choices: [{ message: { role: 'assistant', content: 'secret' } }],
      }),
    },
  },
  responses: { create: async () => ({}) },
};
instrumentOpenAI(client);
await client.chat.completions.create({
  model: 'test',
  messages: [{ role: 'user', content: 'secret' }],
});
await cjs.flush();
assert.equal(exporter.getFinishedSpans().length, 2);
assert.equal(
  exporter.getFinishedSpans()[1].attributes['confident.span.integration'],
  'OpenAI',
);
assert.equal(
  exporter.getFinishedSpans()[1].attributes['gen_ai.input.messages'],
  undefined,
);
assert.equal(
  exporter.getFinishedSpans()[1].attributes['gen_ai.output.messages'],
  undefined,
);
const { createVercelAITracer } = await import('confident-trace/vercel-ai');
const aiSpan = createVercelAITracer().startSpan('chat packaged');
aiSpan.setAttribute('gen_ai.input.messages', 'secret');
aiSpan.end();
await runtime.flush();
assert.equal(
  exporter.getFinishedSpans()[2].attributes['confident.span.integration'],
  'Vercel AI SDK',
);
assert.equal(
  exporter.getFinishedSpans()[2].attributes['gen_ai.input.messages'],
  undefined,
);
const { ConfidentMastraExporter } = await import('confident-trace/mastra');
const mastraSink = new InMemorySpanExporter();
const mastra = new ConfidentMastraExporter({ exporter: mastraSink });
await mastra.exportTracingEvent({
  type: 'span_ended',
  exportedSpan: {
    id: '1234567890abcdef',
    traceId: '1234567890abcdef1234567890abcdef',
    name: 'packaged',
    type: 'workflow_run',
    startTime: new Date(0),
    endTime: new Date(1),
    isRootSpan: true,
    isEvent: false,
    input: 'secret',
  },
});
await mastra.flush();
assert.equal(
  mastraSink.getFinishedSpans()[0].attributes['confident.span.integration'],
  'Mastra',
);
assert.equal(
  mastraSink.getFinishedSpans()[0].attributes['confident.trace.input'],
  undefined,
);
await mastra.shutdown();
assert.equal(runtime.active, true);
for (const [entry, name, label] of [
  ['langchain', 'ConfidentLangChainCallbackHandler', 'LangChain'],
  ['langgraph', 'ConfidentLangGraphCallbackHandler', 'LangGraph'],
]) {
  const Handler = (await import(`confident-trace/${entry}`))[name];
  const handler = new Handler();
  handler.handleChainStart({}, { input: 'secret' }, 'packed');
  handler.handleChainEnd({ output: 'secret' }, 'packed');
  handler.close();
  await runtime.flush();
  const span = exporter.getFinishedSpans().at(-1);
  assert.equal(span.attributes['confident.span.integration'], label);
  assert.equal(span.attributes['confident.span.type'], 'custom');
  assert.equal(span.attributes['confident.span.input'], undefined);
}
const { ConfidentOpenAIAgentsProcessor } =
  await import('confident-trace/openai-agents');
const agents = new ConfidentOpenAIAgentsProcessor();
await agents.onTraceStart({ traceId: 'packed', name: 'packed' });
await agents.onTraceEnd({ traceId: 'packed', name: 'packed' });
await agents.shutdown();
assert.equal(
  exporter.getFinishedSpans().at(-1).attributes['confident.span.integration'],
  'OpenAI Agents SDK',
);
assert.equal(runtime.active, true);
const wrapped = esm.span({ name: 'cross-format', type: 'tool' }, (n) => {
  cjs.updateTrace({ userId: 'shared-context' });
  return n + 1;
});
assert.equal(wrapped(2), 3);
await runtime.flush();
const customSpan = exporter
  .getFinishedSpans()
  .find((s) => s.name === 'cross-format');
assert.equal(
  customSpan.attributes['confident.trace.user_id'],
  'shared-context',
);
assert.equal(customSpan.attributes['confident.span.type'], 'tool');
assert.equal(customSpan.attributes['confident.span.output'], undefined);
assert.equal(await esm.shutdown(), true);
assert.equal(runtime.active, false);
console.log('Packed ESM/CommonJS consumer passed');
