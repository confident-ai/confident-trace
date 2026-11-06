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
import { RunnableLambda, RunnableSequence } from '@langchain/core/runnables';
import { FakeStreamingChatModel } from '@langchain/core/utils/testing';
import {
  AIMessage,
  AIMessageChunk,
  HumanMessage,
} from '@langchain/core/messages';
import type { StandardMessageStructure } from '@langchain/core/messages';
import { ChatGenerationChunk } from '@langchain/core/outputs';
import { DynamicStructuredTool, DynamicTool } from '@langchain/core/tools';
import {
  Annotation,
  MessagesAnnotation,
  StateGraph,
  START,
  END,
  MemorySaver,
  interrupt,
  Command,
  Send,
} from '@langchain/langgraph';
import { createAgent } from 'langchain';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { ConfidentLangChainCallbackHandler } from '@/integrations/langchain';
import { ConfidentLangGraphCallbackHandler } from '@/integrations/langgraph';
import { ContentPolicy } from '@/content/policy';
import { state } from '@/runtime/state';
import { withSpan } from '@/spans/index';

let exporter: InMemorySpanExporter;
let provider: NodeTracerProvider;
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
  await provider.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
const spans = () => exporter.getFinishedSpans();
class TestChatModel extends FakeStreamingChatModel {
  override async _generate() {
    return { generations: [{ text: 'Hello', message: this.responses[0]! }] };
  }
  override async *_streamResponseChunks() {
    for (const message of this.chunks)
      yield new ChatGenerationChunk({ message, text: String(message.content) });
  }
}
const model = () =>
  new TestChatModel({
    responses: [
      new AIMessage<StandardMessageStructure>({
        content: 'Hello',
        usage_metadata: { input_tokens: 7, output_tokens: 3, total_tokens: 10 },
      }),
    ],
  });

it('traces real Runnable chains, chat models and tools under an existing parent', async () => {
  const handler = new ConfidentLangChainCallbackHandler();
  const tool = new DynamicTool({
    name: 'lookup',
    description: 'test',
    func: async (input) => input + '!',
  });
  const chain = RunnableSequence.from([
    model(),
    RunnableLambda.from((message) => String(message.content)),
    tool,
  ]);
  await trace.getTracer('app').startActiveSpan('parent', async (parent) => {
    expect(
      await chain.invoke([new HumanMessage('Hi')], {
        callbacks: [handler],
        metadata: { ls_model_name: 'test-model', ls_provider: 'test' },
      }),
    ).toBe('Hello!');
    parent.end();
  });
  const result = spans();
  const parent = result.find((s) => s.name === 'parent')!;
  const root = result.find(
    (s) =>
      s.attributes['confident.span.type'] === 'custom' &&
      s.parentSpanContext?.spanId === parent.spanContext().spanId,
  )!;
  const llm = result.find(
    (s) => s.attributes['confident.span.type'] === 'llm',
  )!;
  const toolSpan = result.find(
    (s) => s.attributes['confident.span.type'] === 'tool',
  )!;
  expect(
    result.every(
      (s) => s.spanContext().traceId === parent.spanContext().traceId,
    ),
  ).toBe(true);
  expect(root.parentSpanContext?.spanId).toBe(parent.spanContext().spanId);
  expect(llm.parentSpanContext?.spanId).toBe(root.spanContext().spanId);
  expect(toolSpan.parentSpanContext?.spanId).toBe(root.spanContext().spanId);
  expect(llm.attributes).toMatchObject({
    'confident.span.integration': 'LangChain',
    'gen_ai.usage.input_tokens': 7,
    'gen_ai.usage.output_tokens': 3,
    'gen_ai.request.model': 'test-model',
  });
  expect(String(llm.attributes['gen_ai.input.messages'])).toContain('Hi');
  expect(String(llm.attributes['gen_ai.output.messages'])).toContain('Hello');
  expect(
    result
      .filter((s) => s !== parent)
      .every((s) => s.instrumentationScope.schemaUrl?.endsWith('/1.41.0')),
  ).toBe(true);
});
it("records a structured tool's arguments as an object", async () => {
  const handler = new ConfidentLangChainCallbackHandler();
  const forecast = new DynamicStructuredTool({
    name: 'forecast',
    description: 'test',
    schema: {
      type: 'object',
      properties: { city: { type: 'string' } },
      required: ['city'],
    },
    func: async ({ city }: { city: string }) => `${city}: sunny`,
  });
  // As an agent calls it.
  await forecast.invoke(
    {
      name: 'forecast',
      args: { city: 'Paris' },
      id: 'call1',
      type: 'tool_call',
    },
    { callbacks: [handler] },
  );
  const span = spans().find((s) => s.name === 'forecast')!;
  expect(JSON.parse(String(span.attributes['confident.span.input']))).toEqual({
    city: 'Paris',
  });
});

it('captures completed streaming tool calls and usage without buffering tokens', async () => {
  const handler = new ConfidentLangChainCallbackHandler();
  const m = new TestChatModel({
    chunks: [
      new AIMessageChunk<StandardMessageStructure>({ content: 'Hel' }),
      new AIMessageChunk<StandardMessageStructure>({
        content: 'lo',
        tool_call_chunks: [{ name: 'lookup', id: 'c', args: '{}', index: 0 }],
        usage_metadata: { input_tokens: 7, output_tokens: 3, total_tokens: 10 },
      }),
    ],
  });
  let text = '';
  for await (const chunk of await m.stream('Hi', { callbacks: [handler] }))
    text += chunk.content;
  expect(text).toBe('Hello');
  expect(spans()).toHaveLength(1);
  expect(spans()[0]!.attributes['gen_ai.usage.output_tokens']).toBe(3);
  expect(String(spans()[0]!.attributes['gen_ai.output.messages'])).toContain(
    'tool_call',
  );
});
it('keeps parallel LangGraph runs separate and traces invoke and stream nodes', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const schema = Annotation.Root({ value: Annotation<string> });
  const graph = new StateGraph(schema)
    .addNode('first', async (s) => ({ value: s.value + '!' }))
    .addNode('second', (s) => ({ value: s.value + '?' }))
    .addEdge(START, 'first')
    .addEdge('first', 'second')
    .addEdge('second', END)
    .compile();
  await Promise.all(
    ['a', 'b'].map((name) =>
      trace.getTracer('app').startActiveSpan(name, async (parent) => {
        const chunks = [];
        for await (const value of await graph.stream(
          { value: name },
          { callbacks: [handler], streamMode: 'values' },
        ))
          chunks.push(value);
        expect(chunks.at(-1)?.value).toBe(name + '!?');
        parent.end();
      }),
    ),
  );
  expect(
    (await graph.invoke({ value: 'c' }, { callbacks: [handler] })).value,
  ).toBe('c!?');
  const owned = spans().filter(
    (s) => s.attributes['confident.span.integration'] === 'LangGraph',
  );
  expect(
    owned.filter(
      (s) =>
        !owned.some(
          (p) => p.spanContext().spanId === s.parentSpanContext?.spanId,
        ),
    ),
  ).toHaveLength(3);
  expect(new Set(owned.map((s) => s.spanContext().traceId)).size).toBe(3);
  expect(
    owned
      .filter((s) => s.name === 'first' || s.name === 'second')
      .every((s) => s.attributes['confident.span.type'] === 'custom'),
  ).toBe(true);
});
it('works with LangChain createAgent and LangGraph checkpoint resume', async () => {
  const handler = new ConfidentLangGraphCallbackHandler({
    rootSpanType: 'agent',
  });
  const agent = createAgent({ model: model(), tools: [] });
  const result = await agent.invoke(
    { messages: [new HumanMessage('Hi')] },
    { callbacks: [handler] },
  );
  expect(result.messages.at(-1)?.content).toBe('Hello');
  expect(
    spans().some((s) => s.attributes['confident.span.type'] === 'agent'),
  ).toBe(true);
  expect(
    spans().some((s) => s.attributes['confident.span.type'] === 'llm'),
  ).toBe(true);
  const schema = Annotation.Root({ value: Annotation<string> });
  const graph = new StateGraph(schema)
    .addNode('ask', () => ({ value: interrupt('question') as string }))
    .addEdge(START, 'ask')
    .addEdge('ask', END)
    .compile({ checkpointer: new MemorySaver() });
  const config = {
    callbacks: [handler],
    configurable: { thread_id: 'resume' },
  };
  await graph.invoke({ value: '' }, config);
  expect(
    (await graph.invoke(new Command({ resume: 'answer' }), config)).value,
  ).toBe('answer');
  const count = spans().length;
  handler.close();
  expect(spans()).toHaveLength(count);
});
it('treats LangGraph interrupts as control flow and records the interrupt payload', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const schema = Annotation.Root({
    first: Annotation<string>,
    second: Annotation<string>,
  });
  const graph = new StateGraph(schema)
    .addNode('ask', () => {
      const first = withSpan(
        { name: 'ask-user' },
        () => interrupt('question') as string,
      );
      const second = withSpan(
        { name: 'ask-again' },
        () => interrupt('again') as string,
      );
      return { first, second };
    })
    .addEdge(START, 'ask')
    .addEdge('ask', END)
    .compile({ checkpointer: new MemorySaver() });
  const config = {
    callbacks: [handler],
    configurable: { thread_id: 'interrupts' },
  };
  await graph.invoke({ first: '', second: '' }, config);
  await graph.invoke(new Command({ resume: 'yes' }), config);
  expect(
    await graph.invoke(new Command({ resume: 'sure' }), config),
  ).toMatchObject({ first: 'yes', second: 'sure' });
  expect(spans().filter((s) => s.name === 'ask-user')).toHaveLength(3);
  expect(spans().filter((s) => s.name === 'ask-again')).toHaveLength(2);
  expect(spans().every((s) => s.status.code !== SpanStatusCode.ERROR)).toBe(
    true,
  );
  const asked = spans().filter((s) => s.name === 'ask');
  expect(
    JSON.parse(String(asked[0]!.attributes['confident.span.output'])),
  ).toEqual({ __interrupt__: ['question'] });
  handler.close();
});
it('still marks real failures inside LangGraph nodes as errors', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const schema = Annotation.Root({ value: Annotation<string> });
  const failure = new Error('boom');
  const graph = new StateGraph(schema)
    .addNode('fail', () =>
      withSpan({ name: 'work' }, () => {
        throw failure;
      }),
    )
    .addEdge(START, 'fail')
    .addEdge('fail', END)
    .compile();
  await expect(
    graph.invoke({ value: '' }, { callbacks: [handler] }),
  ).rejects.toBe(failure);
  expect(spans().find((s) => s.name === 'work')?.status.code).toBe(
    SpanStatusCode.ERROR,
  );
  expect(spans().find((s) => s.name === 'fail')?.status.code).toBe(
    SpanStatusCode.ERROR,
  );
  handler.close();
});
it('keeps errors content-free and respects opt-out, redaction, bounds and disabled tracing', async () => {
  state.policy = new ContentPolicy({ captureContent: false });
  const handler = new ConfidentLangChainCallbackHandler();
  await model().invoke('secret', { callbacks: [handler] });
  const failure = new Error('secret-error');
  const chain = RunnableLambda.from(() => {
    throw failure;
  });
  await expect(
    chain.invoke('secret', {
      callbacks: [handler],
      metadata: { secret: 'secret' },
    }),
  ).rejects.toBe(failure);
  expect(spans().some((s) => s.status.code === SpanStatusCode.ERROR)).toBe(
    true,
  );
  expect(
    JSON.stringify(spans().map((s) => [s.attributes, s.events, s.status])),
  ).not.toContain('secret');
  delete state.policy;
  await model().invoke('secret', {
    callbacks: [
      new ConfidentLangChainCallbackHandler({ redact: () => '[redacted]' }),
    ],
  });
  expect(spans().at(-1)!.attributes['gen_ai.input.messages']).toBe(
    '"[redacted]"',
  );
  await model().invoke('x'.repeat(10000), {
    callbacks: [
      new ConfidentLangChainCallbackHandler({ maxContentBytes: 128 }),
    ],
  });
  expect(spans().at(-1)!.attributes['gen_ai.input.messages']).toBe(
    '"[truncated]"',
  );
  const count = spans().length;
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  await model().invoke('Hi', { callbacks: [handler] });
  expect(spans()).toHaveLength(count);
});
it('bounds outstanding runs and closes abandoned streams without owning OTel', () => {
  const handler = new ConfidentLangChainCallbackHandler({ maxActiveSpans: 1 });
  handler.handleChainStart({}, {}, 'a');
  handler.handleChainStart({}, {}, 'b');
  handler.close();
  handler.close();
  handler.handleChainEnd({}, 'a');
  expect(spans()).toHaveLength(1);
  expect(spans()[0]!.status.code).toBe(SpanStatusCode.ERROR);
  handler.handleChainStart({}, {}, 'c');
  handler.handleChainEnd({}, 'c');
  trace.getTracer('app').startSpan('still-active').end();
  expect(spans()).toHaveLength(2);
});

it('normalizes real retriever documents and closes model/tool errors', async () => {
  const { BaseRetriever } = await import('@langchain/core/retrievers');
  const { Document } = await import('@langchain/core/documents');
  class Retriever extends BaseRetriever {
    lc_namespace = ['test'];
    override async _getRelevantDocuments(query: string) {
      return [
        new Document({ pageContent: query, metadata: { source: 'test' } }),
      ];
    }
  }
  const handler = new ConfidentLangChainCallbackHandler();
  const result = await new Retriever().invoke('document', {
    callbacks: [handler],
  });
  expect(result[0]!.pageContent).toBe('document');
  expect(spans()[0]!.attributes['langchain.run.type']).toBe('retriever');
  expect(String(spans()[0]!.attributes['confident.span.output'])).toContain(
    'document',
  );
  const failing = new FakeStreamingChatModel({ thrownErrorString: 'secret' });
  await expect(failing.stream('Hi', { callbacks: [handler] })).rejects.toThrow(
    'secret',
  );
  const tool = new DynamicTool({
    name: 'failing',
    description: 'test',
    func: async () => {
      throw new Error('secret');
    },
  });
  await expect(tool.invoke('Hi', { callbacks: [handler] })).rejects.toThrow(
    'secret',
  );
  expect(
    spans().filter((s) => s.status.code === SpanStatusCode.ERROR),
  ).toHaveLength(2);
  const count = spans().length;
  handler.close();
  expect(spans()).toHaveLength(count);
});
it('keeps every LangGraph state key next to normalized messages', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const schema = Annotation.Root({
    ...MessagesAnnotation.spec,
    step: Annotation<number>,
  });
  await new StateGraph(schema)
    .addNode('reply', () => ({ messages: [new AIMessage('hello')], step: 1 }))
    .addEdge(START, 'reply')
    .addEdge('reply', END)
    .compile()
    .invoke(
      { messages: [new HumanMessage('hi')], step: 0 },
      { callbacks: [handler] },
    );
  const root = spans().find((s) => s.name === 'LangGraph')!;
  expect(JSON.parse(String(root.attributes['confident.span.input']))).toEqual({
    messages: [{ role: 'user', parts: [{ type: 'text', content: 'hi' }] }],
    step: 0,
  });
  expect(JSON.parse(String(root.attributes['confident.span.output']))).toEqual({
    messages: [
      { role: 'user', parts: [{ type: 'text', content: 'hi' }] },
      { role: 'assistant', parts: [{ type: 'text', content: 'hello' }] },
    ],
    step: 1,
  });
  handler.close();
});
it('records LangGraph Command and Send values as tagged objects', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const routed = Annotation.Root({
    ...MessagesAnnotation.spec,
    step: Annotation<number>,
  });
  await new StateGraph(routed)
    .addNode(
      'route',
      () =>
        new Command({
          update: { messages: [new AIMessage('routed')], step: 1 },
          goto: 'done',
        }),
      { ends: ['done'] },
    )
    .addNode('done', () => ({ step: 2 }))
    .addEdge(START, 'route')
    .addEdge('done', END)
    .compile()
    .invoke(
      { messages: [new HumanMessage('hi')], step: 0 },
      { callbacks: [handler] },
    );
  const route = spans().find((s) => s.name === 'route')!;
  expect(JSON.parse(String(route.attributes['confident.span.output']))).toEqual(
    {
      type: 'command',
      goto: ['done'],
      update: {
        messages: [
          { role: 'assistant', parts: [{ type: 'text', content: 'routed' }] },
        ],
        step: 1,
      },
    },
  );
  const fanout = Annotation.Root({
    items: Annotation<string[]>,
    out: Annotation<string[]>({
      reducer: (a, b) => a.concat(b),
      default: () => [],
    }),
  });
  await new StateGraph(fanout)
    .addNode('work', (s: { item?: string }) => ({ out: [s.item!] }))
    .addConditionalEdges(START, (s) =>
      s.items.map((item) => new Send('work', { item })),
    )
    .addEdge('work', END)
    .compile()
    .invoke({ items: ['x', 'y'] }, { callbacks: [handler] });
  expect(
    spans().map((s) => s.attributes['confident.span.output']),
  ).toContainEqual(
    JSON.stringify({
      output: [
        { type: 'send', node: 'work', arg: { item: 'x' } },
        { type: 'send', node: 'work', arg: { item: 'y' } },
      ],
    }),
  );
  const asked = new StateGraph(Annotation.Root({ answer: Annotation<string> }))
    .addNode('ask', () => ({ answer: interrupt('question') as string }))
    .addEdge(START, 'ask')
    .addEdge('ask', END)
    .compile({ checkpointer: new MemorySaver() });
  const config = { callbacks: [handler], configurable: { thread_id: 'send' } };
  await asked.invoke({ answer: '' }, config);
  await asked.invoke(new Command({ resume: 'yes' }), config);
  const resumed = spans()
    .filter((s) => s.name === 'LangGraph')
    .at(-1)!;
  expect(
    JSON.parse(String(resumed.attributes['confident.span.input'])),
  ).toEqual({ type: 'command', resume: 'yes' });
  expect(
    spans().some((s) =>
      String(s.attributes['confident.span.output']).includes('[unsupported]'),
    ),
  ).toBe(false);
  handler.close();
});
it('records Date values in LangGraph state as ISO strings', async () => {
  const handler = new ConfidentLangGraphCallbackHandler();
  const schema = Annotation.Root({
    at: Annotation<Date>,
    missing: Annotation<Date>,
  });
  await new StateGraph(schema)
    .addNode('touch', () => ({ at: new Date(Date.UTC(2026, 0, 2)) }))
    .addEdge(START, 'touch')
    .addEdge('touch', END)
    .compile()
    .invoke(
      { at: new Date(0), missing: new Date(Number.NaN) },
      { callbacks: [handler] },
    );
  const root = spans().find((s) => s.name === 'LangGraph')!;
  expect(JSON.parse(String(root.attributes['confident.span.input']))).toEqual({
    at: '1970-01-01T00:00:00.000Z',
    missing: null,
  });
  expect(JSON.parse(String(root.attributes['confident.span.output']))).toEqual({
    at: '2026-01-02T00:00:00.000Z',
    missing: null,
  });
  handler.close();
});
