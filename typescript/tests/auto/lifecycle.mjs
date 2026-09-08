/* global Response */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { init } from 'confident-trace';
import { instrumentOpenAI } from 'confident-trace/openai';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import OpenAI from 'openai';
const require = createRequire(import.meta.url);
const mode = process.env.AUTO_MODE ?? 'normal';
const warnings = [];
const warn = console.warn;
console.warn = (value) => warnings.push(value);
let calls = 0;
const client = new OpenAI({
  apiKey: 'test',
  maxRetries: 0,
  fetch: async (_url, options) => {
    calls++;
    const request = JSON.parse(options.body);
    if (request.input === 'error')
      return Response.json(
        { error: { message: 'private secret' } },
        { status: 400 },
      );
    if (request.stream)
      return new Response(
        'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"Hello"}\n\nevent: response.completed\ndata: {"type":"response.completed","response":{"model":"test","usage":{"input_tokens":2,"output_tokens":1}}}\n\ndata: [DONE]\n\n',
        { headers: { 'content-type': 'text/event-stream' } },
      );
    return Response.json({
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
    });
  },
});
await client.responses.create({ model: 'test', input: 'before' });
const sink = new InMemorySpanExporter();
const rt = init({
  exporter: sink,
  ...(mode === 'manual' ? { instrumentations: [] } : {}),
  ...(mode === 'redact' ? { redact: () => '[redacted]' } : {}),
  ...(mode === 'private' ? { captureContent: false } : {}),
});
assert.equal(require('confident-trace').init(), rt);
if (mode === 'missing') {
  assert.equal(rt.getInstrumentationStatus().hookRegistered, false);
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /--import confident-trace\/register/);
} else assert.equal(rt.getInstrumentationStatus().hookRegistered, true);
assert.equal(sink.getFinishedSpans().length, 0);
if (mode === 'manual') instrumentOpenAI(client);
else if (!['missing', 'disabled'].includes(mode)) instrumentOpenAI(client); // no duplicate automatic wrapper
await Promise.all(
  ['a', 'b'].map((input) => client.responses.create({ model: 'test', input })),
);
const stream = await client.responses.create({
  model: 'test',
  input: 'stream',
  stream: true,
});
for await (const event of stream) void event;
await assert.rejects(
  client.responses.create({ model: 'test', input: 'error' }),
);
assert.equal(await rt.flush(), true);
const result = sink.getFinishedSpans();
const expected = ['missing', 'disabled'].includes(mode) ? 0 : 4;
assert.equal(result.length, expected, `${mode}: duplicate or missing spans`);
if (expected) {
  assert.equal(new Set(result.map((s) => s.spanContext().traceId)).size, 4);
  assert.equal(result.filter((s) => s.status.code === 2).length, 1);
  assert.ok(
    !JSON.stringify(result.map((s) => s.attributes)).includes('private secret'),
  );
  if (mode === 'private')
    assert.ok(
      result.every((s) => s.attributes['gen_ai.input.messages'] === undefined),
    );
  if (mode === 'redact')
    assert.ok(
      result.every((s) =>
        String(s.attributes['gen_ai.input.messages']).includes('[redacted]'),
      ),
    );
}
await rt.shutdown();
await client.responses.create({ model: 'test', input: 'after' });
assert.equal(calls, 6);
console.warn = warn;
console.log(`lifecycle ${mode} passed`);
