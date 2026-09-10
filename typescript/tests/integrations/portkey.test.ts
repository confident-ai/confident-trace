import { context, trace } from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { Portkey } from 'portkey-ai';
import OpenAI from 'openai';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { instrumentPortkey } from '@/integrations/portkey';
import { instrumentOpenAI } from '@/integrations/openai';
import { state } from '@/runtime/state';
let provider: NodeTracerProvider;
let exporter: InMemorySpanExporter;
const chat = {
  id: 'c1',
  object: 'chat.completion',
  created: 0,
  model: 'actual-model',
  choices: [
    {
      index: 0,
      message: {
        role: 'assistant',
        content: 'hello',
        tool_calls: [
          {
            id: 't1',
            type: 'function',
            function: { name: 'lookup', arguments: '{}' },
          },
        ],
      },
      finish_reason: 'tool_calls',
    },
  ],
  usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
};
const response = {
  id: 'r1',
  object: 'response',
  created_at: 0,
  status: 'completed',
  model: 'actual-model',
  output: [
    {
      id: 'm1',
      type: 'message',
      status: 'completed',
      role: 'assistant',
      content: [{ type: 'output_text', text: 'hello', annotations: [] }],
    },
  ],
  usage: { input_tokens: 4, output_tokens: 2, total_tokens: 6 },
};
beforeEach(() => {
  trace.disable();
  context.disable();
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
  vi.unstubAllGlobals();
});
function stub(api = 'chat', stream = false, error = false) {
  const fetcher = vi.fn(async () => {
    if (error)
      return Response.json(
        { error: { message: 'private secret', type: 'bad_request' } },
        { status: 400 },
      );
    if (!stream) return Response.json(api === 'chat' ? chat : response);
    const chunks =
      api === 'chat'
        ? [
            {
              ...chat,
              object: 'chat.completion.chunk',
              choices: [
                {
                  index: 0,
                  finish_reason: 'stop',
                  delta: {
                    content: 'hello',
                    tool_calls: [
                      { index: 0, ...chat.choices[0]!.message.tool_calls[0] },
                    ],
                  },
                },
              ],
            },
          ]
        : [
            { type: 'response.output_text.delta', delta: 'hello' },
            { type: 'response.completed', response },
          ];
    return new Response(
      chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join('') +
        'data: [DONE]\n\n',
      { headers: { 'content-type': 'text/event-stream' } },
    );
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
it.each([
  ['chat', false],
  ['chat', true],
  ['responses', false],
  ['responses', true],
] as const)('captures native %s stream=%s', async (api, stream) => {
  const fetcher = stub(api, stream);
  const client = new Portkey({
    apiKey: 'test',
    provider: 'openai',
    traceID: 'user-trace',
  });
  const original = client.chat.completions.create;
  const undo = instrumentPortkey(client);
  instrumentPortkey(client);
  const result =
    api === 'chat'
      ? await client.chat.completions.create({
          model: 'alias',
          messages: [],
          stream,
        })
      : await client.responses.create({ model: 'alias', input: 'hi', stream });
  if (stream)
    for await (const chunk of result as AsyncIterable<unknown>) void chunk;
  const records = exporter.getFinishedSpans();
  expect(records).toHaveLength(1);
  expect(records[0]!.attributes).toMatchObject({
    'confident.span.integration': 'Portkey',
    'gen_ai.provider.name': 'portkey',
    'gen_ai.request.model': 'alias',
    'gen_ai.response.model': 'actual-model',
    'gen_ai.usage.input_tokens': 4,
  });
  expect(records[0]!.attributes['gen_ai.output.messages']).toContain('hello');
  if (api === 'chat')
    expect(records[0]!.attributes['gen_ai.output.messages']).toContain(
      'lookup',
    );
  expect(fetcher).toHaveBeenCalledTimes(1);
  undo();
  expect(client.chat.completions.create).toBe(original);
});
it.each(['chat', 'responses'])(
  'records %s errors without leaking messages',
  async (api) => {
    stub(api, false, true);
    const client = new Portkey({ apiKey: 'test' });
    instrumentPortkey(client);
    await expect(
      api === 'chat'
        ? client.chat.completions.create({ model: 'alias', messages: [] })
        : client.responses.create({ model: 'alias', input: 'hi' }),
    ).rejects.toThrow();
    const records = exporter.getFinishedSpans();
    expect(records).toHaveLength(1);
    expect(records[0]!.status.code).toBe(2);
    expect(JSON.stringify(records[0]!.attributes)).not.toContain(
      'private secret',
    );
  },
);
it('respects content privacy', async () => {
  stub();
  const client = new Portkey({ apiKey: 'test' });
  instrumentPortkey(client, { captureContent: false });
  await client.chat.completions.create({ model: 'alias', messages: [] });
  expect(
    exporter.getFinishedSpans()[0]!.attributes['gen_ai.output.messages'],
  ).toBeUndefined();
});
it.each([
  ['https://api.portkey.ai/v1/', 'portkey'],
  ['https://api.portkey.ai.evil.test/v1', 'openai'],
  ['https://api.portkey.ai/other', 'openai'],
  ['http://custom.test/v1', 'portkey'],
])('identifies proxy %s', async (baseURL, expected) => {
  const client = new OpenAI({
    apiKey: 'test',
    baseURL,
    fetch: async () => Response.json(chat),
  });
  instrumentOpenAI(client, { portkeyProxyUrls: ['http://custom.test/v1'] });
  await client.chat.completions.create({ model: 'alias', messages: [] });
  const attrs = exporter.getFinishedSpans()[0]!.attributes;
  expect(attrs['confident.span.integration']).toBe('OpenAI');
  expect(attrs['gen_ai.provider.name']).toBe(expected);
  expect(attrs['confident.gateway.name']).toBe(
    expected === 'portkey' ? expected : undefined,
  );
});

it('ends spans on early streaming exit', async () => {
  stub('chat', true);
  const client = new Portkey({ apiKey: 'test' });
  instrumentPortkey(client);
  const stream = await client.chat.completions.create({
    model: 'alias',
    messages: [],
    stream: true,
  });
  for await (const chunk of stream) {
    void chunk;
    break;
  }
  expect(exporter.getFinishedSpans()).toHaveLength(1);
});
