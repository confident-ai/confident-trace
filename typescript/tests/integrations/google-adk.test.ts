import { context, propagation, trace } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';
import { FunctionTool, Gemini, InMemoryRunner, LlmAgent } from '@google/adk';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { InitOptions } from '@/config/types';

const CAPTURE = 'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT';
const LEGACY = 'ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS';
const OPERATION = 'gen_ai.operation.name';

const response = (parts: unknown[]) => ({
  candidates: [{ content: { role: 'model', parts }, finishReason: 'STOP' }],
  usageMetadata: { promptTokenCount: 2, candidatesTokenCount: 1 },
});

// The model asks for one tool call, then answers once the result is back.
async function gemini(
  input: string | URL | Request,
  init?: RequestInit,
): Promise<Response> {
  const body = String(init?.body ?? '');
  const reply = body.includes('functionResponse')
    ? response([{ text: 'done' }])
    : response([{ functionCall: { name: 'lookup', args: { city: 'Macau' } } }]);
  if (String(input).includes('streamGenerateContent'))
    return new Response(`data: ${JSON.stringify(reply)}\n\n`, {
      headers: { 'content-type': 'text/event-stream' },
    });
  return new Response(JSON.stringify(reply), {
    headers: { 'content-type': 'application/json' },
  });
}

let saved: Record<string, string | undefined>;
beforeEach(() => {
  saved = { [CAPTURE]: process.env[CAPTURE], [LEGACY]: process.env[LEGACY] };
  delete process.env[CAPTURE];
  delete process.env[LEGACY];
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  vi.stubGlobal('fetch', vi.fn(gemini));
});
afterEach(async () => {
  const { shutdown } = await import('@/runtime/init');
  await shutdown(1000);
  vi.unstubAllGlobals();
  for (const [key, value] of Object.entries(saved))
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  trace.disable();
  context.disable();
  propagation.disable();
});

async function start(options: InitOptions = {}) {
  const exporter = new InMemorySpanExporter();
  const { init, flush, shutdown } = await import('@/runtime/init');
  const { attachGoogleAdk } = await import('@/auto/google-adk');
  const { instrumentGoogleGenAI } = await import('@/integrations/google-genai');
  init({ exporter, ...options });
  attachGoogleAdk();
  const model = new Gemini({ model: 'gemini-2.5-flash', apiKey: 'offline' });
  // ADK's own GenAI client, wrapped as automatic instrumentation would.
  instrumentGoogleGenAI(model.apiClient);
  const lookup = new FunctionTool({
    name: 'lookup',
    description: 'Look up a city.',
    parameters: {
      type: 'OBJECT',
      properties: { city: { type: 'STRING' } },
    } as never,
    execute: (input) => ({ city: String((input as { city: string }).city) }),
  });
  const runner = new InMemoryRunner({
    appName: 'native-test',
    agent: new LlmAgent({ name: 'assistant', model, tools: [lookup] }),
  });
  const run = async (sessionId: string) => {
    await runner.sessionService.createSession({
      appName: 'native-test',
      userId: 'user',
      sessionId,
    });
    for await (const event of runner.runAsync({
      userId: 'user',
      sessionId,
      newMessage: { role: 'user', parts: [{ text: sessionId }] },
    }))
      void event;
  };
  const spans = async () => {
    await flush(1000);
    return exporter.getFinishedSpans();
  };
  return { exporter, model, run, spans, shutdown };
}
const byOperation = (spans: ReadableSpan[], operation: string) =>
  spans.filter((s) => s.attributes[OPERATION] === operation);

it('records ADK model and tool IO on its native spans', async () => {
  const { model, run, spans, shutdown } = await start();
  expect(process.env[CAPTURE]).toBe('SPAN_ONLY');
  await run('session-a');
  const captured = await spans();
  const inference = byOperation(captured, 'generate_content');
  expect(inference).toHaveLength(2);
  for (const span of inference) {
    expect(span.instrumentationScope.name).toBe('gcp.vertex.agent');
    expect(span.attributes['confident.span.integration']).toBe('Google ADK');
    expect(span.attributes['gen_ai.conversation.id']).toBe('session-a');
    expect(span.attributes['gen_ai.input.messages']).toContain('session-a');
    expect(span.attributes['gen_ai.output.messages']).toBeTruthy();
  }
  const [tool, ...others] = byOperation(captured, 'execute_tool');
  expect(others).toHaveLength(0);
  expect(tool!.attributes['gen_ai.tool.name']).toBe('lookup');
  expect(
    JSON.parse(String(tool!.attributes['gen_ai.tool.call.arguments'])),
  ).toEqual({ city: 'Macau' });
  expect(
    JSON.parse(String(tool!.attributes['gen_ai.tool.call.result'])),
  ).toEqual({ city: 'Macau' });
  // ADK already records its model calls; ours would duplicate them.
  expect(
    captured.filter((s) => s.instrumentationScope.name === 'confident-trace'),
  ).toHaveLength(0);
  // The wrapper is live: a model call outside ADK is still recorded.
  await model.apiClient.models.generateContent({
    model: 'gemini-2.5-flash',
    contents: 'direct',
  });
  expect(
    (await spans()).filter(
      (s) => s.instrumentationScope.name === 'confident-trace',
    ),
  ).toHaveLength(1);
  await shutdown(1000);
  expect(process.env[CAPTURE]).toBeUndefined();
});

it('keeps concurrent ADK sessions in separate traces', async () => {
  const { run, spans } = await start();
  await Promise.all([run('session-a'), run('session-b')]);
  const agents = byOperation(await spans(), 'invoke_agent');
  expect(agents).toHaveLength(2);
  expect(new Set(agents.map((s) => s.spanContext().traceId)).size).toBe(2);
  expect(
    new Set(agents.map((s) => s.attributes['gen_ai.conversation.id'])),
  ).toEqual(new Set(['session-a', 'session-b']));
});

it.each([
  ['an empty content setting', { [CAPTURE]: '' }, {}],
  ['NO_CONTENT', { [CAPTURE]: 'NO_CONTENT' }, {}],
  ['the legacy ADK opt-out', { [LEGACY]: 'false' }, {}],
  ['captureContent: false', {}, { captureContent: false }],
] as const)(
  'leaves ADK content off for %s',
  async (_name, env: Record<string, string>, options: InitOptions) => {
    Object.assign(process.env, env);
    const { run, spans } = await start(options);
    expect(process.env[CAPTURE]).toBe(env[CAPTURE]);
    await run('private');
    const captured = await spans();
    expect(byOperation(captured, 'generate_content')).toHaveLength(2);
    for (const span of captured) {
      expect(span.attributes['gen_ai.input.messages']).toBeUndefined();
      expect(span.attributes['gen_ai.tool.call.arguments']).toBeUndefined();
    }
  },
);

it('does not turn content on when ADK spans go to another provider', async () => {
  await start({ globalProvider: false });
  expect(process.env[CAPTURE]).toBeUndefined();
});

it('applies trace context to the trace ADK starts', async () => {
  const { run, spans } = await start();
  const { traceContext } = await import('@/spans/index');
  await traceContext({ userId: 'user-42', tags: ['support'] }, () =>
    run('with-context'),
  );
  const captured = await spans();
  const root = captured.find((s) => !s.parentSpanContext);
  expect(root?.instrumentationScope.name).toBe('gcp.vertex.agent');
  expect(root?.attributes['confident.trace.user_id']).toBe('user-42');
  expect(root?.attributes['confident.trace.tags']).toEqual(['support']);
});
