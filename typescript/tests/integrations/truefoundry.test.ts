import { context, trace } from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import Anthropic from '@anthropic-ai/sdk';
import OpenAI from 'openai';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { instrumentAnthropic } from '@/integrations/anthropic';
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

const urls = ['http://truefoundry.test/gateway'];
it.each(['chat', 'responses'])(
  'identifies TrueFoundry OpenAI %s and restores',
  async (api) => {
    const fetcher = vi.fn(async () =>
      Response.json(api === 'chat' ? chat : response),
    );
    const client = new OpenAI({
      apiKey: 'secret',
      baseURL: urls[0],
      fetch: fetcher,
    });
    const restore = instrumentOpenAI(client, {
      truefoundryProxyUrls: urls,
      captureContent: false,
    });
    const call = () =>
      api === 'chat'
        ? client.chat.completions.create({ model: 'alias', messages: [] })
        : client.responses.create({ model: 'alias', input: 'hi' });
    await call();
    const spans = exporter.getFinishedSpans();
    expect(spans).toHaveLength(1);
    expect(spans[0]!.attributes).toMatchObject({
      'confident.gateway.name': 'truefoundry',
      'gen_ai.provider.name': 'truefoundry',
      'confident.span.integration': 'OpenAI',
      'gen_ai.usage.input_tokens': 4,
    });
    expect(spans[0]!.attributes['gen_ai.output.messages']).toBeUndefined();
    expect(JSON.stringify(spans[0]!.attributes)).not.toContain('secret');
    restore();
    await call();
    expect(exporter.getFinishedSpans()).toHaveLength(1);
  },
);
it('identifies TrueFoundry Anthropic messages', async () => {
  const client = new Anthropic({
    apiKey: 'secret',
    baseURL: urls[0],
    fetch: async () =>
      Response.json({
        id: 'm1',
        type: 'message',
        role: 'assistant',
        model: 'actual',
        content: [{ type: 'text', text: 'hello' }],
        stop_reason: 'end_turn',
        usage: { input_tokens: 2, output_tokens: 1 },
      }),
  });
  instrumentAnthropic(client, { truefoundryProxyUrls: urls });
  await client.messages.create({ model: 'alias', max_tokens: 8, messages: [] });
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  expect(exporter.getFinishedSpans()[0]!.attributes).toMatchObject({
    'confident.gateway.name': 'truefoundry',
    'gen_ai.provider.name': 'truefoundry',
    'confident.span.integration': 'Anthropic',
    'gen_ai.usage.input_tokens': 2,
  });
});
it.each([
  'http://truefoundry.test/gateway-other',
  'https://truefoundry.test/gateway',
  'http://truefoundry.test:8080/gateway',
  'http://truefoundry.test/',
])('does not guess gateway for %s', async (baseURL) => {
  const client = new OpenAI({
    apiKey: 'test',
    baseURL,
    fetch: async () => Response.json(chat),
  });
  instrumentOpenAI(client, { truefoundryProxyUrls: urls });
  await client.chat.completions.create({
    model: 'truefoundry/alias',
    messages: [],
  });
  expect(
    exporter.getFinishedSpans()[0]!.attributes['confident.gateway.name'],
  ).toBeUndefined();
});

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
  'identifies TrueFoundry Anthropic %s streams',
  async (method) => {
    const client = new Anthropic({
      apiKey: 'test',
      baseURL: urls[0],
      fetch: async () =>
        new Response(
          anthropicEvents()
            .map(
              (event) =>
                `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`,
            )
            .join(''),
          { headers: { 'content-type': 'text/event-stream' } },
        ),
    });
    instrumentAnthropic(client, { truefoundryProxyUrls: urls });
    if (method === 'stream') {
      expect(
        (
          await client.messages
            .stream({ model: 'alias', max_tokens: 8, messages: [] })
            .finalMessage()
        ).content,
      ).toEqual(message.content);
    } else {
      for await (const event of await client.messages.create({
        model: 'alias',
        max_tokens: 8,
        messages: [],
        stream: true,
      }))
        void event;
    }
    expect(exporter.getFinishedSpans()).toHaveLength(1);
    expect(exporter.getFinishedSpans()[0]!.attributes).toMatchObject({
      'confident.gateway.name': 'truefoundry',
      'gen_ai.provider.name': 'truefoundry',
      'confident.span.integration': 'Anthropic',
      'gen_ai.usage.output_tokens': 3,
    });
  },
);

it.each(['openai', 'anthropic'])(
  'preserves %s authentication and errors',
  async (sdk) => {
    const fetcher: typeof fetch = async (_url, options) => {
      expect(new Headers(options?.headers).get('authorization')).toBe(
        'Bearer secret',
      );
      return Response.json(
        { error: { type: 'invalid_request_error', message: 'bad request' } },
        { status: 400 },
      );
    };
    const config = {
      apiKey: 'secret',
      baseURL: urls[0],
      defaultHeaders: { Authorization: 'Bearer secret' },
      maxRetries: 0,
      fetch: fetcher,
    };
    if (sdk === 'openai') {
      const client = new OpenAI(config);
      instrumentOpenAI(client, {
        truefoundryProxyUrls: urls,
        captureContent: false,
      });
      await expect(
        client.chat.completions.create({ model: 'alias', messages: [] }),
      ).rejects.toMatchObject({ status: 400 });
    } else {
      const client = new Anthropic(config);
      instrumentAnthropic(client, {
        truefoundryProxyUrls: urls,
        captureContent: false,
      });
      await expect(
        client.messages.create({ model: 'alias', messages: [], max_tokens: 8 }),
      ).rejects.toMatchObject({ status: 400 });
    }
    const spans = exporter.getFinishedSpans();
    expect(spans).toHaveLength(1);
    expect(spans[0]!.status.code).toBe(2);
    expect(spans[0]!.attributes['confident.gateway.name']).toBe('truefoundry');
    expect(JSON.stringify(spans[0]!.attributes)).not.toContain('secret');
  },
);
