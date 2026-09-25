import {
  context,
  propagation,
  trace,
  ProxyTracerProvider,
} from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { telemetry } from '@livekit/agents';
import OpenAI from 'openai';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { InitOptions } from '@/config/types';

const chat = {
  id: 'chat-1',
  object: 'chat.completion',
  created: 0,
  model: 'test-model',
  choices: [
    {
      index: 0,
      message: { role: 'assistant', content: 'Hello' },
      finish_reason: 'stop',
    },
  ],
  usage: { prompt_tokens: 7, completion_tokens: 3, total_tokens: 10 },
};

beforeEach(() => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  // LiveKit's tracer is module state; start each case unconfigured.
  telemetry.tracer.setProvider(trace.getTracerProvider());
});
afterEach(async () => {
  const { shutdown } = await import('@/runtime/init');
  await shutdown(1000);
  trace.disable();
  context.disable();
  propagation.disable();
});

async function start(options: InitOptions = {}) {
  const exporter = new InMemorySpanExporter();
  const { init, flush } = await import('@/runtime/init');
  const { attachLiveKit } = await import('@/auto/livekit');
  const { instrumentOpenAI } = await import('@/integrations/openai');
  init({ exporter, ...options });
  attachLiveKit({ telemetry });
  const client = new OpenAI({
    apiKey: 'test',
    maxRetries: 0,
    fetch: async () =>
      new Response(JSON.stringify(chat), {
        headers: { 'content-type': 'application/json' },
      }),
  });
  instrumentOpenAI(client);
  // The span tree LiveKit's LLM node creates around each model call.
  const converse = () =>
    telemetry.tracer.startActiveSpan(
      () =>
        telemetry.tracer.startActiveSpan(
          () =>
            client.chat.completions.create({
              model: 'test-model',
              messages: [{ role: 'user', content: 'Hi' }],
            }),
          { name: 'llm_request_run' },
        ),
      { name: 'llm_request', attributes: { 'gen_ai.operation.name': 'chat' } },
    );
  return { exporter, flush, converse };
}
const scoped = (exporter: InMemorySpanExporter, name: string) =>
  exporter
    .getFinishedSpans()
    .filter((span) => span.instrumentationScope.name === name);

it('labels LiveKit spans and keeps one span per model call', async () => {
  const { exporter, flush, converse } = await start();
  expect(telemetry.tracer.getProvider()).not.toBeInstanceOf(
    ProxyTracerProvider,
  );
  // LiveKit Cloud adds its exporter through registerSpanProcessor.
  const { state } = await import('@/runtime/state');
  const cloud = new InMemorySpanExporter();
  state.ownedProvider!.registerSpanProcessor(new SimpleSpanProcessor(cloud));
  await converse();
  await flush();
  expect(scoped(cloud, 'livekit-agents')).toHaveLength(2);
  const livekit = scoped(exporter, 'livekit-agents');
  expect(livekit.map((span) => span.name).sort()).toEqual([
    'llm_request',
    'llm_request_run',
  ]);
  for (const span of livekit)
    expect(span.attributes['confident.span.integration']).toBe('LiveKit');
  const root = livekit.find((span) => span.name === 'llm_request')!;
  expect(root.attributes['confident.trace.name']).toBeUndefined();
  expect(scoped(exporter, 'confident-trace')).toEqual([]);
});

it('preserves a configured LiveKit provider and keeps our span', async () => {
  const elsewhere = new InMemorySpanExporter();
  const other = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(elsewhere)],
  });
  telemetry.setTracerProvider(other);
  const { exporter, flush, converse } = await start();
  expect(telemetry.tracer.getProvider()).toBe(other);
  await converse();
  await flush();
  expect(scoped(exporter, 'livekit-agents')).toEqual([]);
  expect(scoped(exporter, 'confident-trace')).toHaveLength(1);
  expect(scoped(elsewhere, 'livekit-agents')).toHaveLength(2);
  await other.shutdown();
});

it('leaves LiveKit spans unlabelled when not selected', async () => {
  const { exporter, flush, converse } = await start({
    instrumentations: ['openai'],
  });
  await converse();
  await flush();
  expect(scoped(exporter, 'confident-trace')).toHaveLength(1);
  const livekit = scoped(exporter, 'livekit-agents');
  expect(livekit).toHaveLength(2);
  for (const span of livekit)
    expect(span.attributes['confident.span.integration']).toBeUndefined();
});

it('flushes after cleanup, preserves failures, and installs only once', async () => {
  const exporter = new InMemorySpanExporter();
  const { init } = await import('@/runtime/init');
  const { attachLiveKit } = await import('@/auto/livekit');
  const runtime = init({ exporter });
  attachLiveKit({ telemetry });
  const failure = new Error('cleanup failed');
  const lifecycle = {
    async flushJobLogs() {
      telemetry.tracer.startSpan({ name: 'late_cleanup' }).end();
      throw failure;
    },
  };
  attachLiveKit(lifecycle);
  const wrapped = lifecycle.flushJobLogs;
  attachLiveKit(lifecycle);
  expect(lifecycle.flushJobLogs).toBe(wrapped);
  await expect(lifecycle.flushJobLogs()).rejects.toBe(failure);
  expect(scoped(exporter, 'livekit-agents').map((s) => s.name)).toEqual([
    'late_cleanup',
  ]);
  const flush = vi
    .spyOn(runtime, 'flush')
    .mockRejectedValue(new Error('export failure'));
  await expect(lifecycle.flushJobLogs()).rejects.toBe(failure);
  expect(flush).toHaveBeenCalledWith(5000);
  flush.mockRestore();
});

it('does not flush deselected LiveKit lifecycle hooks', async () => {
  const { init } = await import('@/runtime/init');
  const { attachLiveKit } = await import('@/auto/livekit');
  const runtime = init({
    exporter: new InMemorySpanExporter(),
    instrumentations: [],
  });
  const flush = vi.spyOn(runtime, 'flush');
  const lifecycle = {
    async flushJobLogs() {
      return 42;
    },
  };
  attachLiveKit(lifecycle);
  expect(await lifecycle.flushJobLogs()).toBe(42);
  expect(flush).not.toHaveBeenCalled();
});

it('installs privacy filtering when LiveKit inherited our provider', async () => {
  vi.stubEnv('LIVEKIT_TELEMETRY_ALLOW_PII', '0');
  try {
    const exporter = new InMemorySpanExporter();
    const { init } = await import('@/runtime/init');
    const { state } = await import('@/runtime/state');
    const { attachLiveKit } = await import('@/auto/livekit');
    const runtime = init({ exporter });
    telemetry.tracer.setProvider(state.ownedProvider!.tracerProvider);
    attachLiveKit({ telemetry });
    const span = telemetry.tracer.startSpan({ name: 'agent_session' });
    span.setAttribute('lk.pii.user_transcript', 'private transcript');
    span.end();
    await runtime.flush();
    expect(exporter.getFinishedSpans()).toHaveLength(1);
    expect(
      exporter.getFinishedSpans()[0]!.attributes['lk.pii.user_transcript'],
    ).toBeUndefined();
  } finally {
    vi.unstubAllEnvs();
  }
});

it('reports an unsupported worker lifecycle instead of silently skipping the flush', async () => {
  const { init } = await import('@/runtime/init');
  const { attachLiveKit } = await import('@/auto/livekit');
  const runtime = init({ exporter: new InMemorySpanExporter() });
  attachLiveKit({}, '/node_modules/@livekit/agents/dist/job_lifecycle.js');
  expect(runtime.getInstrumentationStatus().integrations.livekit).toBe(
    'failed',
  );
});
