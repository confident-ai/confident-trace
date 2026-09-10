/* global Response, require */
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS acceptance fixture. */
const assert = require('node:assert/strict');
const { init } = require('confident-trace');
const { instrumentPortkey } = require('confident-trace/portkey');
const { InMemorySpanExporter } = require('@opentelemetry/sdk-trace-base');
const { Portkey } = require('portkey-ai');
(async () => {
  const sink = new InMemorySpanExporter();
  const runtime = init({ exporter: sink, instrumentations: ['portkey'] });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) =>
    Response.json(
      String(url).includes('/responses')
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
            usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 },
          },
    );
  try {
    const client = new Portkey({ apiKey: 'test' });
    instrumentPortkey(client);
    await client.chat.completions.create({ model: 'alias', messages: [] });
    await client.responses.create({ model: 'alias', input: 'hello' });
    await runtime.flush();
    const spans = sink.getFinishedSpans();
    assert.equal(spans.length, 2);
    for (const span of spans) {
      assert.equal(span.attributes['confident.span.integration'], 'Portkey');
      assert.equal(span.attributes['gen_ai.provider.name'], 'portkey');
      assert.equal(span.attributes['gen_ai.usage.input_tokens'], 2);
    }
    assert.equal(
      runtime.getInstrumentationStatus().integrations.portkey,
      'enabled',
    );
    await runtime.shutdown();
    console.log('Portkey CommonJS automatic/manual coexistence passed');
  } finally {
    globalThis.fetch = originalFetch;
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
