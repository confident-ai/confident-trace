/* global Response */
import assert from 'node:assert/strict';
import { init, turn } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const sink = new InMemorySpanExporter();
const runtime = init({ exporter: sink });
const response = {
  id: 'r',
  object: 'response',
  model: 'mock',
  output: [
    {
      type: 'message',
      role: 'assistant',
      content: [{ type: 'output_text', text: 'Hello' }],
    },
  ],
  usage: { input_tokens: 2, output_tokens: 1 },
};
const chat = {
  id: 'c',
  created: 0,
  object: 'chat.completion',
  model: 'mock',
  system_fingerprint: null,
  choices: [
    {
      index: 0,
      finish_reason: 'stop',
      message: { role: 'assistant', content: 'Hello' },
    },
  ],
  usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 },
};

const name = process.argv[2];
const googleResponse = {
  candidates: [
    {
      content: { role: 'model', parts: [{ text: 'Hello' }] },
      finishReason: 'STOP',
    },
  ],
  usageMetadata: { promptTokenCount: 2, candidatesTokenCount: 1 },
  modelVersion: 'mock',
};
const anthropicResponse = {
  id: 'm',
  type: 'message',
  role: 'assistant',
  model: 'mock',
  content: [{ type: 'text', text: 'Hello' }],
  usage: { input_tokens: 2, output_tokens: 1 },
  stop_reason: 'end_turn',
};
const payload =
  name === 'google'
    ? googleResponse
    : name === 'anthropic'
      ? anthropicResponse
      : name === 'openai'
        ? response
        : chat;
const realFetch = globalThis.fetch;
globalThis.fetch = async () => Response.json(payload);
try {
  let call;
  if (name === 'openai') {
    const { default: OpenAI } = await import('openai');
    const client = new OpenAI({ apiKey: 'test', fetch: globalThis.fetch });
    call = () => client.responses.create({ model: 'mock', input: 'Hi' });
  } else if (name === 'anthropic') {
    const { default: Anthropic } = await import('@anthropic-ai/sdk');
    const client = new Anthropic({ apiKey: 'test', fetch: globalThis.fetch });
    call = () =>
      client.messages.create({
        model: 'mock',
        max_tokens: 20,
        messages: [{ role: 'user', content: 'Hi' }],
      });
  } else if (name === 'openrouter') {
    const { OpenRouter } = await import('@openrouter/sdk');
    const { HTTPClient } = await import('@openrouter/sdk/lib/http.js');
    const client = new OpenRouter({
      apiKey: 'test',
      httpClient: new HTTPClient({ fetcher: globalThis.fetch }),
    });
    call = () =>
      client.chat.send({
        chatRequest: {
          model: 'mock',
          messages: [{ role: 'user', content: 'Hi' }],
        },
      });
  } else if (name === 'portkey') {
    const { Portkey } = await import('portkey-ai');
    const client = new Portkey({ apiKey: 'test' });
    call = () =>
      client.chat.completions.create({
        model: 'mock',
        messages: [{ role: 'user', content: 'Hi' }],
      });
  } else if (name === 'google') {
    const { GoogleGenAI } = await import('@google/genai');
    const client = new GoogleGenAI({ apiKey: 'test' });
    call = () =>
      client.models.generateContent({ model: 'mock', contents: 'Hi' });
  } else throw new Error('Expected provider name');
  await turn({ name, threadId: 'test' }, call);
  await runtime.flush();
  const root = sink.getFinishedSpans().find((s) => s.name === name);
  const output = root.attributes['confident.trace.output'];
  assert.ok(output.includes('Hello'), `${name}: ${output}`);
  assert.ok(!output.includes('[unsupported]'), `${name}: ${output}`);
  assert.equal(root.attributes['confident.trace.name'], name);
  console.log(`${name}: parent output passed`);
} finally {
  globalThis.fetch = realFetch;
  await runtime.shutdown();
}
