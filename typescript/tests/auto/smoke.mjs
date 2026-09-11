/* global Response */
import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenAI } from '@google/genai';
import { RunnableLambda } from '@langchain/core/runnables';
import { Annotation, StateGraph, START, END } from '@langchain/langgraph';
import { generateText } from 'ai';
import { MockLanguageModelV3 } from 'ai/test';
import { Mastra } from '@mastra/core';
import { Agent, Runner, Usage, setTraceProcessors } from '@openai/agents';

const sink = new InMemorySpanExporter();
const client = new OpenAI({
  apiKey: 'test',
  fetch: async () =>
    Response.json({
      id: 'r',
      object: 'response',
      model: 'test',
      output: [
        {
          type: 'message',
          role: 'assistant',
          content: [{ type: 'output_text', text: 'Hello' }],
        },
      ],
      usage: { input_tokens: 2, output_tokens: 1, total_tokens: 3 },
    }),
});
const mastra = new Mastra({ logger: false });
const rt = init({
  exporter: sink,
  captureContent: process.env.AUTO_PRIVATE !== 'true',
});
assert.ok(
  [
    'openai',
    'anthropic',
    'google-genai',
    'vercel-ai',
    'langchain',
    'langgraph',
    'mastra',
    'openai-agents',
  ].every(
    (name) => rt.getInstrumentationStatus().integrations[name] === 'enabled',
  ),
  JSON.stringify(rt.getInstrumentationStatus()),
);
await client.responses.create({ model: 'test', input: 'Hi' });
const anthropic = new Anthropic({
  apiKey: 'test',
  fetch: async () =>
    Response.json({
      id: 'm',
      type: 'message',
      role: 'assistant',
      model: 'test',
      content: [{ type: 'text', text: 'Hello' }],
      usage: { input_tokens: 2, output_tokens: 1 },
      stop_reason: 'end_turn',
    }),
});
await anthropic.messages.create({
  model: 'test',
  max_tokens: 10,
  messages: [{ role: 'user', content: 'Hi' }],
});
const google = new GoogleGenAI({ apiKey: 'test' });
// The Google SDK consults global fetch at invocation.
const realFetch = globalThis.fetch;
globalThis.fetch = async () =>
  Response.json({
    candidates: [
      {
        content: { role: 'model', parts: [{ text: 'Hello' }] },
        finishReason: 'STOP',
      },
    ],
    usageMetadata: {
      promptTokenCount: 2,
      candidatesTokenCount: 1,
      totalTokenCount: 3,
    },
    modelVersion: 'test',
  });
await google.models.generateContent({ model: 'test', contents: 'Hi' });
globalThis.fetch = realFetch;
await RunnableLambda.from((x) => `${x}!`).invoke('hello');
const graph = new StateGraph(Annotation.Root({ value: Annotation() }))
  .addNode('answer', (s) => ({ value: s.value + '!' }))
  .addEdge(START, 'answer')
  .addEdge('answer', END)
  .compile();
assert.equal((await graph.invoke({ value: 'hello' })).value, 'hello!');
const usage = { inputTokens: { total: 2 }, outputTokens: { total: 1 } };
const aiResult = await generateText({
  model: new MockLanguageModelV3({
    doGenerate: {
      content: [{ type: 'text', text: 'Hello' }],
      finishReason: { unified: 'stop', raw: 'stop' },
      usage,
      warnings: [],
    },
  }),
  prompt: 'Hi',
});
assert.equal(aiResult.text, 'Hello');
const instance = mastra.observability.getDefaultInstance();
assert.ok(instance, 'Mastra automatically bootstrapped');
const span = instance.startSpan({
  type: 'agent_run',
  name: 'mastra-agent',
  input: 'Hi',
});
span.end({ output: 'Hello' });
// Remove the SDK's unrelated default network exporter; our auto processor remains.
setTraceProcessors([]);
const agent = new Agent({
  name: 'test',
  model: {
    getResponse: async () => ({
      usage: new Usage(),
      output: [
        {
          type: 'message',
          role: 'assistant',
          status: 'completed',
          content: [{ type: 'output_text', text: 'Hello' }],
        },
      ],
    }),
  },
});
assert.equal((await new Runner().run(agent, 'Hi')).finalOutput, 'Hello');
await rt.flush();
const spans = sink.getFinishedSpans();
assert.equal(
  spans.filter((s) => s.name === 'Agent workflow' && !s.parentSpanContext)
    .length,
  1,
  'duplicate agent trace',
);
assert.equal(
  spans.filter((s) => s.name === 'mastra-agent').length,
  1,
  'duplicate Mastra span',
);
if (process.env.AUTO_PRIVATE === 'true')
  assert.ok(
    spans.every(
      (s) =>
        s.attributes['gen_ai.input.messages'] === undefined &&
        s.attributes['gen_ai.output.messages'] === undefined &&
        s.attributes['confident.span.input'] === undefined,
    ),
  );
const integrations = new Set(
  spans.map((s) => s.attributes['confident.span.integration']),
);
for (const name of [
  'OpenAI',
  'Anthropic',
  'Google GenAI',
  'LangChain',
  'LangGraph',
  'Vercel AI SDK',
  'Mastra',
  'OpenAI Agents SDK',
])
  assert.ok(integrations.has(name), `missing ${name}`);
await rt.shutdown();
const afterShutdown = await generateText({
  model: new MockLanguageModelV3({
    doGenerate: {
      content: [{ type: 'text', text: 'Still works' }],
      finishReason: { unified: 'stop', raw: 'stop' },
      usage,
      warnings: [],
    },
  }),
  prompt: 'after shutdown',
});
assert.equal(afterShutdown.text, 'Still works');
console.log('All eight automatic integrations passed');
