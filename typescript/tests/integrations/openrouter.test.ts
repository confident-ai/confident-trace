import { context, trace } from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { OpenRouter } from '@openrouter/sdk';
import { HTTPClient } from '@openrouter/sdk/lib/http.js';
import OpenAI from 'openai';
import { afterEach, beforeEach, expect, it } from 'vitest';
import { instrumentOpenRouter } from '@/integrations/openrouter';
import { instrumentOpenAI } from '@/integrations/openai';
import { state } from '@/runtime/state';

let exporter: InMemorySpanExporter;
let provider: NodeTracerProvider;
const body = {
  id: 'r1',
  created: 0,
  model: 'actual/model',
  object: 'chat.completion',
  system_fingerprint: null,
  choices: [
    {
      index: 0,
      finish_reason: 'tool_calls',
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
    },
  ],
  usage: { prompt_tokens: 4, completion_tokens: 2, total_tokens: 6 },
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
});
function client(stream = false, error = false) {
  return new OpenRouter({
    apiKey: 'test',
    httpClient: new HTTPClient({
      fetcher: async () => {
        if (error)
          return Response.json(
            { error: { code: 400, message: 'secret' } },
            { status: 400 },
          );
        if (!stream) return Response.json(body);
        const chunk = {
          ...body,
          object: 'chat.completion.chunk',
          system_fingerprint: 'test',
          choices: [
            {
              index: 0,
              finish_reason: 'stop',
              delta: {
                role: 'assistant',
                content: 'hello',
                tool_calls: [
                  {
                    index: 0,
                    id: 't1',
                    type: 'function',
                    function: { name: 'lookup', arguments: '{}' },
                  },
                ],
              },
            },
          ],
        };
        return new Response(
          `data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`,
          { headers: { 'content-type': 'text/event-stream' } },
        );
      },
    }),
  });
}
it.each([false, true])(
  'captures native SDK responses (stream=%s)',
  async (stream) => {
    const sdk = client(stream);
    const undo = instrumentOpenRouter(sdk);
    instrumentOpenRouter(sdk); // idempotent
    const result = await sdk.chat.send({
      chatRequest: {
        model: 'requested/alias',
        messages: [],
        stream,
      },
    });
    if (stream)
      for await (const chunk of result as AsyncIterable<unknown>) void chunk;
    const records = exporter.getFinishedSpans();
    expect(records).toHaveLength(1);
    expect(records[0]!.attributes).toMatchObject({
      'confident.span.integration': 'OpenRouter',
      'gen_ai.provider.name': 'openrouter',
      'gen_ai.request.model': 'requested/alias',
      'gen_ai.response.model': 'actual/model',
      'gen_ai.usage.input_tokens': 4,
      'gen_ai.usage.output_tokens': 2,
    });
    expect(records[0]!.attributes['gen_ai.output.messages']).toContain('hello');
    expect(records[0]!.attributes['gen_ai.output.messages']).toContain(
      'lookup',
    );
    undo();
    await sdk.chat.send({
      chatRequest: { model: 'requested/alias', messages: [] },
    });
    expect(exporter.getFinishedSpans()).toHaveLength(1);
  },
);
it('records native SDK errors without error content', async () => {
  const sdk = client(false, true);
  instrumentOpenRouter(sdk);
  await expect(
    sdk.chat.send({ chatRequest: { model: 'requested/alias', messages: [] } }),
  ).rejects.toThrow();
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  expect(exporter.getFinishedSpans()[0]!.status.code).toBe(2);
  expect(
    JSON.stringify(exporter.getFinishedSpans()[0]!.attributes),
  ).not.toContain('secret');
});
it('respects disabled content capture', async () => {
  const sdk = client();
  instrumentOpenRouter(sdk, { captureContent: false });
  await sdk.chat.send({
    chatRequest: { model: 'requested/alias', messages: [] },
  });
  expect(
    exporter.getFinishedSpans()[0]!.attributes['gen_ai.output.messages'],
  ).toBeUndefined();
});
it.each([
  ['https://openrouter.ai/api/v1/', 'openrouter'],
  ['https://openrouter.ai.evil.test/api/v1', 'openai'],
  ['https://openrouter.ai/other', 'openai'],
  ['http://gateway.test/v1', 'openrouter'],
])('identifies OpenAI client endpoint %s', async (baseURL, expected) => {
  const sdk = new OpenAI({
    baseURL,
    apiKey: 'test',
    fetch: async () => Response.json(body),
  });
  instrumentOpenAI(sdk, { openrouterProxyUrls: ['http://gateway.test/v1/'] });
  await sdk.chat.completions.create({ model: 'requested/alias', messages: [] });
  const attrs = exporter.getFinishedSpans()[0]!.attributes;
  expect(attrs['gen_ai.provider.name']).toBe(expected);
  expect(attrs['confident.gateway.name']).toBe(
    expected === 'openrouter' ? expected : undefined,
  );
  expect(attrs['confident.span.integration']).toBe('OpenAI');
});

it('records in-band streaming errors without capturing their messages', async () => {
  const chunk = {
    ...body,
    object: 'chat.completion.chunk',
    system_fingerprint: 'test',
    choices: [],
    error: { code: 502, message: 'private upstream error' },
  };
  const sdk = new OpenRouter({
    apiKey: 'test',
    httpClient: new HTTPClient({
      fetcher: async () =>
        new Response(`data: ${JSON.stringify(chunk)}\n\ndata: [DONE]\n\n`, {
          headers: { 'content-type': 'text/event-stream' },
        }),
    }),
  });
  instrumentOpenRouter(sdk);
  const stream = await sdk.chat.send({
    chatRequest: { model: 'test', messages: [], stream: true },
  });
  for await (const _ of stream as AsyncIterable<unknown>) void _;
  const records = exporter.getFinishedSpans();
  expect(records).toHaveLength(1);
  expect(records[0]!.status.code).toBe(2);
  expect(JSON.stringify(records[0]!.attributes)).not.toContain(
    'private upstream error',
  );
});
it('ends spans when streaming consumption stops early', async () => {
  const sdk = client(true);
  instrumentOpenRouter(sdk);
  const stream = await sdk.chat.send({
    chatRequest: { model: 'test', messages: [], stream: true },
  });
  for await (const _ of stream as AsyncIterable<unknown>) {
    void _;
    break;
  }
  expect(exporter.getFinishedSpans()).toHaveLength(1);
});
