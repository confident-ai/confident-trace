/* global Response */
import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { FunctionTool, Gemini, InMemoryRunner, LlmAgent } from '@google/adk';

// Real ADK runner with automatic instrumentation; only Gemini's HTTP is faked.
const reply = (parts) => ({
  candidates: [{ content: { role: 'model', parts }, finishReason: 'STOP' }],
  usageMetadata: { promptTokenCount: 2, candidatesTokenCount: 1 },
});
globalThis.fetch = async (_url, request) =>
  Response.json(
    String(request?.body ?? '').includes('functionResponse')
      ? reply([{ text: 'done' }])
      : reply([{ functionCall: { name: 'lookup', args: { city: 'Macau' } } }]),
  );

const sink = new InMemorySpanExporter();
const rt = init({ exporter: sink });
assert.equal(
  rt.getInstrumentationStatus().integrations['google-adk'],
  'enabled',
);
assert.equal(
  process.env.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT,
  'SPAN_ONLY',
);

const model = new Gemini({ model: 'gemini-2.5-flash', apiKey: 'offline' });
const runner = new InMemoryRunner({
  appName: 'auto',
  agent: new LlmAgent({
    name: 'assistant',
    model,
    tools: [
      new FunctionTool({
        name: 'lookup',
        description: 'Look up a city.',
        parameters: {
          type: 'OBJECT',
          properties: { city: { type: 'STRING' } },
        },
        execute: ({ city }) => ({ city }),
      }),
    ],
  }),
});
await runner.sessionService.createSession({
  appName: 'auto',
  userId: 'user',
  sessionId: 'auto-session',
});
for await (const event of runner.runAsync({
  userId: 'user',
  sessionId: 'auto-session',
  newMessage: { role: 'user', parts: [{ text: 'hi' }] },
}))
  void event;
// ADK's GenAI client is instrumented too: outside ADK it is ours to record.
await model.apiClient.models.generateContent({
  model: 'gemini-2.5-flash',
  contents: 'direct',
});
await rt.flush();

const spans = sink.getFinishedSpans();
const adk = spans.filter(
  (s) => s.instrumentationScope.name === 'gcp.vertex.agent',
);
for (const span of adk)
  assert.equal(span.attributes['confident.span.integration'], 'Google ADK');
const inference = adk.filter(
  (s) => s.attributes['gen_ai.operation.name'] === 'generate_content',
);
assert.equal(inference.length, 2);
for (const span of inference) {
  assert.ok(span.attributes['gen_ai.input.messages']);
  assert.ok(span.attributes['gen_ai.output.messages']);
}
const [tool] = adk.filter(
  (s) => s.attributes['gen_ai.operation.name'] === 'execute_tool',
);
assert.deepEqual(JSON.parse(tool.attributes['gen_ai.tool.call.arguments']), {
  city: 'Macau',
});
assert.deepEqual(JSON.parse(tool.attributes['gen_ai.tool.call.result']), {
  city: 'Macau',
});
// Only the direct call outside ADK keeps a Confident provider span.
assert.equal(
  spans.filter((s) => s.instrumentationScope.name === 'confident-trace').length,
  1,
);
await rt.shutdown();
assert.equal(
  process.env.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT,
  undefined,
);
console.log('Google ADK automatic instrumentation passed');
