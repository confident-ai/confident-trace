/* global Response, require */
/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS acceptance fixture. */
const assert = require('node:assert/strict');
const { init } = require('confident-trace');
const { instrumentOpenRouter } = require('confident-trace/openrouter');
const { InMemorySpanExporter } = require('@opentelemetry/sdk-trace-base');

(async () => {
  const sink = new InMemorySpanExporter();
  const runtime = init({ exporter: sink, instrumentations: ['openrouter'] });
  const { OpenRouter } = await import('@openrouter/sdk');
  const { HTTPClient } = await import('@openrouter/sdk/lib/http.js');
  const client = new OpenRouter({
    apiKey: 'test',
    httpClient: new HTTPClient({
      fetcher: async () =>
        Response.json({
          id: 'r1',
          created: 0,
          object: 'chat.completion',
          model: 'actual/model',
          system_fingerprint: null,
          choices: [
            {
              index: 0,
              finish_reason: 'stop',
              message: { role: 'assistant', content: 'Hello' },
            },
          ],
          usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 },
        }),
    }),
  });
  instrumentOpenRouter(client); // Must coexist with automatic instrumentation.
  await client.chat.send({ chatRequest: { model: 'alias', messages: [] } });
  await runtime.flush();
  const spans = sink.getFinishedSpans();
  assert.equal(spans.length, 1);
  assert.equal(spans[0].attributes['confident.span.integration'], 'OpenRouter');
  assert.equal(spans[0].attributes['gen_ai.request.model'], 'alias');
  assert.equal(spans[0].attributes['gen_ai.response.model'], 'actual/model');
  assert.equal(spans[0].attributes['gen_ai.usage.input_tokens'], 2);
  assert.equal(
    runtime.getInstrumentationStatus().integrations.openrouter,
    'enabled',
  );
  await runtime.shutdown();
  console.log(
    'OpenRouter CommonJS dynamic import and automatic/manual coexistence passed',
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
