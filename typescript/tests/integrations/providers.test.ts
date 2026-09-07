import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
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
import OpenAI from 'openai';
import Anthropic from '@anthropic-ai/sdk';
import { GoogleGenAI } from '@google/genai';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { instrumentOpenAI } from '@/integrations/openai';
import { instrumentAnthropic } from '@/integrations/anthropic';
import { instrumentGoogleGenAI } from '@/integrations/google-genai';
import { instrument } from '@/integrations/instrument';
import { state } from '@/runtime/state';

let provider: NodeTracerProvider;
let exporter: InMemorySpanExporter;
let body: unknown;
let events: unknown[] | undefined;
let status = 200;
let requests = 0;
const server = createServer(async (req, res) => {
  for await (const chunk of req) void chunk;
  requests++;
  res.writeHead(status, {
    'content-type': events ? 'text/event-stream' : 'application/json',
    'x-request-id': 'test-request',
  });
  if (events) {
    for (const event of events)
      res.write(
        `${typeof (event as { type?: string }).type === 'string' ? `event: ${(event as { type: string }).type}\n` : ''}data: ${JSON.stringify(event)}\n\n`,
      );
    if (req.url?.includes('chat/completions')) res.write('data: [DONE]\n\n');
    res.end();
  } else res.end(JSON.stringify(body));
});
let baseURL: string;
beforeEach(async () => {
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
  body = {};
  events = undefined;
  status = 200;
  requests = 0;
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  baseURL = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});
afterEach(async () => {
  await provider.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  await new Promise<void>((resolve, reject) =>
    server.close((e) => (e ? reject(e) : resolve())),
  );
  vi.unstubAllEnvs();
});
function openai() {
  const client = new OpenAI({ apiKey: 'test', baseURL, maxRetries: 0 });
  instrumentOpenAI(client);
  return client;
}
function anthropic() {
  const client = new Anthropic({ apiKey: 'test', baseURL, maxRetries: 0 });
  instrumentAnthropic(client);
  return client;
}
function google() {
  const client = new GoogleGenAI({
    apiKey: 'test',
    httpOptions: { baseUrl: baseURL },
  });
  instrumentGoogleGenAI(client);
  return client;
}
const chat = {
  id: 'chat-1',
  model: 'test-model',
  choices: [
    {
      index: 0,
      message: { role: 'assistant', content: 'Hello' },
      finish_reason: 'stop',
    },
  ],
  usage: { prompt_tokens: 7, completion_tokens: 3 },
};
const message = {
  id: 'msg-1',
  type: 'message',
  role: 'assistant',
  model: 'test-model',
  content: [{ type: 'text', text: 'Hello' }],
  stop_reason: 'end_turn',
  stop_sequence: null,
  usage: { input_tokens: 7, output_tokens: 3 },
};
const response = {
  id: 'resp-1',
  object: 'response',
  model: 'test-model',
  status: 'completed',
  output: [
    {
      type: 'message',
      role: 'assistant',
      content: [{ type: 'output_text', text: 'Hello', annotations: [] }],
    },
  ],
  usage: { input_tokens: 7, output_tokens: 3 },
};
const generated = {
  responseId: 'gen-1',
  modelVersion: 'test-model',
  candidates: [
    {
      content: { role: 'model', parts: [{ text: 'Hello' }] },
      finishReason: 'STOP',
    },
  ],
  usageMetadata: { promptTokenCount: 7, candidatesTokenCount: 3 },
};
function attrs() {
  return exporter.getFinishedSpans()[0]!.attributes;
}
function output() {
  return JSON.parse(String(attrs()['gen_ai.output.messages']));
}

it.each(['chat', 'responses', 'anthropic', 'google'] as const)(
  'captures %s using the real SDK and keeps parentage',
  async (surface) => {
    body = { chat, responses: response, anthropic: message, google: generated }[
      surface
    ];
    await trace.getTracer('app').startActiveSpan('parent', async (parent) => {
      if (surface === 'chat')
        await openai().chat.completions.create({
          model: 'test-model',
          messages: [{ role: 'user', content: 'Hi' }],
        });
      if (surface === 'responses')
        await openai().responses.create({ model: 'test-model', input: 'Hi' });
      if (surface === 'anthropic')
        await anthropic().messages.create({
          model: 'test-model',
          max_tokens: 12,
          messages: [{ role: 'user', content: 'Hi' }],
        });
      if (surface === 'google')
        await google().models.generateContent({
          model: 'test-model',
          contents: 'Hi',
          config: { systemInstruction: 'Be brief', maxOutputTokens: 12 },
        });
      parent.end();
    });
    const [child, parent] = exporter.getFinishedSpans();
    expect(child!.kind).toBe(SpanKind.CLIENT);
    expect(child!.parentSpanContext?.spanId).toBe(parent!.spanContext().spanId);
    expect(child!.instrumentationScope.schemaUrl).toBe(
      'https://opentelemetry.io/schemas/1.37.0',
    );
    expect(attrs()).toMatchObject({
      'confident.span.type': 'llm',
      'gen_ai.request.model': 'test-model',
      'gen_ai.response.model': 'test-model',
      'gen_ai.usage.input_tokens': 7,
      'gen_ai.usage.output_tokens': 3,
      'confident.span.integration':
        surface === 'google'
          ? 'Google GenAI'
          : surface === 'anthropic'
            ? 'Anthropic'
            : 'OpenAI',
    });
    expect(output()).toEqual([
      { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
    ]);
    expect(requests).toBe(1);
  },
);
it('preserves APIPromise helpers and raw response bodies', async () => {
  body = chat;
  const client = openai();
  const promise = client.chat.completions.create({
    model: 'test-model',
    messages: [],
  });
  const wrapped = await promise.withResponse();
  expect(wrapped.request_id).toBe('test-request');
  expect(await promise).toBe(wrapped.data);
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  const raw = await client.chat.completions
    .create({ model: 'test-model', messages: [] })
    .asResponse();
  expect(raw.bodyUsed).toBe(false);
  expect(await raw.json()).toEqual(chat);
  expect(exporter.getFinishedSpans()).toHaveLength(2);
});
it('captures Chat streaming text/tool deltas and trailing usage without eager consumption', async () => {
  events = [
    {
      id: 's',
      model: 'test-model',
      choices: [
        {
          index: 0,
          delta: {
            content: 'Hel',
            tool_calls: [
              {
                index: 0,
                id: 'call-1',
                function: { name: 'lookup', arguments: '{"x":' },
              },
            ],
          },
        },
      ],
    },
    {
      choices: [
        {
          index: 0,
          delta: {
            content: 'lo',
            tool_calls: [{ index: 0, function: { arguments: '1}' } }],
          },
          finish_reason: 'tool_calls',
        },
      ],
    },
    { choices: [], usage: { prompt_tokens: 7, completion_tokens: 3 } },
  ];
  const result = await openai().chat.completions.create({
    model: 'test-model',
    messages: [],
    stream: true,
  });
  expect(exporter.getFinishedSpans()).toHaveLength(0);
  const chunks = [];
  for await (const chunk of result) chunks.push(chunk);
  expect(chunks).toHaveLength(3);
  expect(output()).toEqual([
    { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
    {
      role: 'assistant',
      parts: [
        {
          type: 'tool_call',
          id: 'call-1',
          name: 'lookup',
          arguments: '{"x":1}',
        },
      ],
    },
  ]);
  expect(attrs()['gen_ai.usage.output_tokens']).toBe(3);
  expect(attrs()['confident.span.type']).toBe('llm');
});
it('captures Responses streams without duplicating completed output', async () => {
  events = [
    {
      type: 'response.created',
      response: { id: 'resp-1', model: 'test-model' },
    },
    { type: 'response.output_text.delta', delta: 'Hello' },
    { type: 'response.completed', response },
  ];
  const stream = await openai().responses.create({
    model: 'test-model',
    input: 'Hi',
    stream: true,
  });
  for await (const event of stream) void event;
  expect(output()).toEqual([
    { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
  ]);
  expect(attrs()['gen_ai.usage.output_tokens']).toBe(3);
  expect(attrs()['confident.span.type']).toBe('llm');
});
const anthropicEvents = () => [
  {
    type: 'message_start',
    message: {
      ...message,
      content: [],
      stop_reason: null,
      usage: { input_tokens: 7, output_tokens: 0 },
    },
  },
  {
    type: 'content_block_start',
    index: 0,
    content_block: { type: 'text', text: '' },
  },
  {
    type: 'content_block_delta',
    index: 0,
    delta: { type: 'text_delta', text: 'Hello' },
  },
  { type: 'content_block_stop', index: 0 },
  {
    type: 'message_delta',
    delta: { stop_reason: 'end_turn', stop_sequence: null },
    usage: { output_tokens: 3 },
  },
  { type: 'message_stop' },
];
it.each(['create', 'stream'] as const)(
  'captures Anthropic %s streaming including helper-only consumption',
  async (method) => {
    events = anthropicEvents();
    const client = anthropic();
    if (method === 'stream') {
      const stream = client.messages.stream({
        model: 'test-model',
        max_tokens: 12,
        messages: [],
      });
      expect(stream.on('text', () => {})).toBe(stream);
      expect((await stream.finalMessage()).content).toEqual(message.content);
    } else {
      const stream = await client.messages.create({
        model: 'test-model',
        max_tokens: 12,
        messages: [],
        stream: true,
      });
      for await (const event of stream) void event;
    }
    expect(exporter.getFinishedSpans()).toHaveLength(1);
    expect(output()).toEqual([
      { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
    ]);
    expect(attrs()['gen_ai.usage.output_tokens']).toBe(3);
    expect(attrs()['confident.span.type']).toBe('llm');
  },
);
it('captures Google camelCase streaming metadata', async () => {
  events = [
    { candidates: [{ content: { role: 'model', parts: [{ text: 'Hel' }] } }] },
    {
      ...generated,
      candidates: [
        {
          content: { role: 'model', parts: [{ text: 'lo' }] },
          finishReason: 'STOP',
        },
      ],
    },
  ];
  const stream = await google().models.generateContentStream({
    model: 'test-model',
    contents: 'Hi',
  });
  for await (const chunk of stream) void chunk;
  expect(output()).toEqual([
    { role: 'assistant', parts: [{ type: 'text', content: 'Hello' }] },
  ]);
  expect(attrs()['gen_ai.response.id']).toBe('gen-1');
  expect(attrs()['gen_ai.usage.output_tokens']).toBe(3);
  expect(attrs()['confident.span.type']).toBe('llm');
});
it('honors init content policy and explicit redaction without leaking error bodies', async () => {
  const { ContentPolicy } = await import('@/content/policy');
  state.policy = new ContentPolicy({ captureContent: false });
  body = chat;
  await openai().chat.completions.create({
    model: 'test-model',
    messages: [{ role: 'user', content: 'secret' }],
  });
  expect(attrs()['gen_ai.input.messages']).toBeUndefined();
  expect(attrs()['gen_ai.output.messages']).toBeUndefined();
  status = 400;
  body = { error: { message: 'secret', type: 'invalid_request_error' } };
  await expect(
    openai().responses.create({ model: 'test-model', input: 'secret' }),
  ).rejects.toThrow('secret');
  const span = exporter.getFinishedSpans()[1]!;
  expect(span.status.code).toBe(SpanStatusCode.ERROR);
  expect(span.attributes['confident.span.type']).toBe('llm');
  expect(JSON.stringify(span.attributes)).not.toContain('secret');
  expect(span.events).toHaveLength(0);
});
it('bounds stream retention, still captures usage, and ends early-return streams', async () => {
  const client = new OpenAI({ apiKey: 'test', baseURL, maxRetries: 0 });
  instrumentOpenAI(client, { maxContentBytes: 128 });
  events = [
    { choices: [{ delta: { content: 'x'.repeat(10000) } }] },
    { choices: [], usage: { completion_tokens: 42 } },
  ];
  for await (const chunk of await client.chat.completions.create({
    model: 'm',
    messages: [],
    stream: true,
  }))
    void chunk;
  expect(attrs()['confident.span.content_truncated']).toBe(true);
  expect(attrs()['gen_ai.usage.output_tokens']).toBe(42);
  expect(
    Buffer.byteLength(String(attrs()['gen_ai.output.messages'])),
  ).toBeLessThanOrEqual(128);
  const stream = await client.chat.completions.create({
    model: 'm',
    messages: [],
    stream: true,
  });
  for await (const chunk of stream) {
    void chunk;
    break;
  }
  expect(exporter.getFinishedSpans()).toHaveLength(2);
});
it('preserves errors, iterator cancellation, suppression, and restoration ownership', async () => {
  const error = new Error('sdk failure');
  const target = {
    create: vi.fn(async () => {
      throw error;
    }),
  };
  const original = target.create;
  const undo = instrument([[target, 'create']], 'openai');
  const duplicate = instrument([[target, 'create']], 'openai');
  duplicate();
  await expect(target.create()).rejects.toBe(error);
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  undo();
  undo();
  expect(target.create).toBe(original);
  const iterator = {
    next: vi.fn(async () => ({ done: false, value: { choices: [] } })),
    return: vi.fn(async () => ({ done: true, value: undefined })),
  };
  const stream = { [Symbol.asyncIterator]: () => iterator };
  const second = { create: async () => stream };
  instrument([[second, 'create']], 'openai');
  const result = await second.create();
  await result[Symbol.asyncIterator]().return!();
  expect(iterator.next).not.toHaveBeenCalled();
  expect(iterator.return).toHaveBeenCalledOnce();
  expect(exporter.getFinishedSpans()).toHaveLength(2);
});
it('does not instrument disabled calls or invoke content getters', async () => {
  const getter = vi.fn(() => {
    throw new Error('getter');
  });
  const request = Object.defineProperty({ model: 'test-model' }, 'messages', {
    get: getter,
  });
  const target = {
    create: vi.fn(async (value: unknown) => {
      void value;
      return chat;
    }),
  };
  instrument([[target, 'create']], 'openai');
  await target.create(request);
  expect(getter).not.toHaveBeenCalled();
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  await target.create(request);
  expect(exporter.getFinishedSpans()).toHaveLength(1);
});

it('uses redaction once on normalized content and omits content when redaction throws', async () => {
  body = chat;
  const client = new OpenAI({ apiKey: 'test', baseURL, maxRetries: 0 });
  const undo = instrumentOpenAI(client, { redact: () => '[redacted]' });
  await client.responses.create({ model: 'm', input: 'secret' });
  expect(attrs()['gen_ai.input.messages']).toBe('"[redacted]"');
  expect(attrs()['confident.trace.input']).toBe('"[redacted]"');
  undo();
  instrumentOpenAI(client, {
    redact: () => {
      throw new Error('no content');
    },
  });
  await client.chat.completions.create({ model: 'm', messages: [] });
  expect(
    exporter.getFinishedSpans()[1]!.attributes['gen_ai.output.messages'],
  ).toBeUndefined();
});
it('keeps streaming withResponse and await on the same promise open until consumed', async () => {
  events = [{ choices: [{ delta: { content: 'Hello' } }] }];
  const promise = openai().chat.completions.create({
    model: 'm',
    messages: [],
    stream: true,
  });
  const { data } = await promise.withResponse();
  expect(await promise).toBe(data);
  expect(exporter.getFinishedSpans()).toHaveLength(0);
  for await (const chunk of data) void chunk;
  expect(exporter.getFinishedSpans()).toHaveLength(1);
});
it('ends an unconsumed stream on explicit SDK abort', async () => {
  events = [{ choices: [] }];
  const stream = await openai().chat.completions.create({
    model: 'm',
    messages: [],
    stream: true,
  });
  stream.controller.abort();
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  expect(exporter.getFinishedSpans()[0]!.status.code).toBe(
    SpanStatusCode.ERROR,
  );
});
it('preserves iterator failures and runs stream work inside the provider span', async () => {
  const error = new Error('stream failure');
  let childParent: string | undefined;
  const stream = {
    async *[Symbol.asyncIterator]() {
      childParent = trace.getSpan(context.active())?.spanContext().spanId;
      yield { choices: [{ delta: { content: 'prefix' } }] };
      throw error;
    },
  };
  const target = { create: async () => stream };
  instrument([[target, 'create']], 'openai');
  const result = await target.create();
  await expect(
    (async () => {
      for await (const event of result) void event;
    })(),
  ).rejects.toBe(error);
  const span = exporter.getFinishedSpans()[0]!;
  expect(childParent).toBe(span.spanContext().spanId);
  expect(trace.getSpan(context.active())).toBeUndefined();
  expect(span.status.code).toBe(SpanStatusCode.ERROR);
  expect(span.attributes['confident.span.type']).toBe('llm');
  expect(output()[0].parts[0].content).toBe('prefix');
});
it('suppresses nested provider calls and preserves later patches on restoration', async () => {
  const inner = { create: async () => chat };
  instrument([[inner, 'create']], 'openai');
  const outer = { create: async () => inner.create() };
  const undo = instrument([[outer, 'create']], 'anthropic');
  await outer.create();
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  const later = async () => chat;
  outer.create = later;
  undo();
  expect(outer.create).toBe(later);
});
it('captures Anthropic tool fragments and Google complete tool calls', async () => {
  events = [
    anthropicEvents()[0],
    {
      type: 'content_block_start',
      index: 0,
      content_block: {
        type: 'tool_use',
        id: 'tool-1',
        name: 'lookup',
        input: {},
      },
    },
    {
      type: 'content_block_delta',
      index: 0,
      delta: { type: 'input_json_delta', partial_json: '{"x":1}' },
    },
    { type: 'content_block_stop', index: 0 },
    {
      type: 'message_delta',
      delta: { stop_reason: 'tool_use' },
      usage: { output_tokens: 3 },
    },
    { type: 'message_stop' },
  ];
  for await (const event of await anthropic().messages.create({
    model: 'm',
    messages: [],
    max_tokens: 12,
    stream: true,
  }))
    void event;
  expect(output()[0].parts[0]).toMatchObject({
    type: 'tool_call',
    id: 'tool-1',
    name: 'lookup',
    arguments: '{"x":1}',
  });
  events = undefined;
  body = {
    ...generated,
    candidates: [
      {
        content: {
          role: 'model',
          parts: [{ functionCall: { name: 'lookup', args: { x: 1 } } }],
        },
        finishReason: 'STOP',
      },
    ],
  };
  await google().models.generateContent({ model: 'm', contents: 'Hi' });
  expect(
    JSON.parse(
      String(
        exporter.getFinishedSpans()[1]!.attributes['gen_ai.output.messages'],
      ),
    )[0].parts[0],
  ).toMatchObject({ type: 'tool_call', name: 'lookup', arguments: { x: 1 } });
});
it('keeps concurrent provider requests under their own parents', async () => {
  body = chat;
  const client = openai();
  await Promise.all(
    ['first', 'second'].map((name) =>
      trace.getTracer('app').startActiveSpan(name, async (parent) => {
        await client.chat.completions.create({ model: name, messages: [] });
        parent.end();
      }),
    ),
  );
  const spans = exporter.getFinishedSpans();
  for (const name of ['first', 'second'])
    expect(
      spans.find((s) => s.name === `chat ${name}`)!.parentSpanContext?.spanId,
    ).toBe(spans.find((s) => s.name === name)!.spanContext().spanId);
});

it('preserves initialization privacy when only a content limit is overridden', async () => {
  const { ContentPolicy } = await import('@/content/policy');
  state.policy = new ContentPolicy({ captureContent: false });
  const target = { create: async () => chat };
  instrument([[target, 'create']], 'openai', { maxContentBytes: 512 });
  await target.create();
  expect(attrs()['gen_ai.output.messages']).toBeUndefined();
});
