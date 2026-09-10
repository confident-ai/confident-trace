/* global Response, require */
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS acceptance fixture. */
const assert = require('node:assert/strict');
const { init } = require('confident-trace');
const { instrumentOpenAI } = require('confident-trace/openai');
const { InMemorySpanExporter } = require('@opentelemetry/sdk-trace-base');
const OpenAI = require('openai').default;
const Anthropic = require('@anthropic-ai/sdk').default;
const { instrumentAnthropic } = require('confident-trace/anthropic');
(async () => {
  const sink = new InMemorySpanExporter();
  const runtime = init({
    exporter: sink,
    instrumentations: ['openai', 'anthropic'],
    bifrostProxyUrls: [
      'http://bifrost.test/openai',
      'http://bifrost.test/anthropic',
    ],
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) =>
    Response.json(
      String(url).includes('/messages')
        ? {
            id: 'm1',
            type: 'message',
            role: 'assistant',
            model: 'actual',
            content: [{ type: 'text', text: 'hello' }],
            stop_reason: 'end_turn',
            usage: { input_tokens: 2, output_tokens: 1 },
          }
        : String(url).includes('/responses')
          ? {
              id: 'r1',
              object: 'response',
              created_at: 0,
              status: 'completed',
              model: 'actual-model',
              output: [
                {
                  id: 'm1',
                  type: 'message',
                  role: 'assistant',
                  status: 'completed',
                  content: [
                    { type: 'output_text', text: 'hello', annotations: [] },
                  ],
                },
              ],
              usage: { input_tokens: 2, output_tokens: 1, total_tokens: 3 },
            }
          : {
              id: 'c1',
              object: 'chat.completion',
              created: 0,
              model: 'actual-model',
              choices: [
                {
                  index: 0,
                  message: { role: 'assistant', content: 'hello' },
                  finish_reason: 'stop',
                },
              ],
              usage: {
                prompt_tokens: 2,
                completion_tokens: 1,
                total_tokens: 3,
              },
            },
    );
  try {
    const client = new OpenAI({
      apiKey: 'test',
      baseURL: 'http://bifrost.test/openai',
    });
    instrumentOpenAI(client);
    await client.chat.completions.create({ model: 'alias', messages: [] });
    await client.responses.create({ model: 'alias', input: 'hello' });
    const anthropic = new Anthropic({
      apiKey: 'test',
      baseURL: 'http://bifrost.test/anthropic',
    });
    instrumentAnthropic(anthropic);
    await anthropic.messages.create({
      model: 'alias',
      max_tokens: 8,
      messages: [],
    });
    await runtime.flush();
    const spans = sink.getFinishedSpans();
    assert.equal(spans.length, 3);
    for (const span of spans) {
      assert.ok(
        ['OpenAI', 'Anthropic'].includes(
          span.attributes['confident.span.integration'],
        ),
      );
      assert.equal(span.attributes['gen_ai.provider.name'], 'bifrost');
      assert.equal(span.attributes['confident.gateway.name'], 'bifrost');
      assert.equal(span.attributes['gen_ai.usage.input_tokens'], 2);
    }
    assert.equal(
      runtime.getInstrumentationStatus().integrations.openai,
      'enabled',
    );
    await runtime.shutdown();
    console.log('Bifrost CommonJS automatic/manual coexistence passed');
  } finally {
    globalThis.fetch = originalFetch;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
