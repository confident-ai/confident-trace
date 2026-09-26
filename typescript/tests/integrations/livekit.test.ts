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

type Upload = { url: string; headers: Record<string, unknown>; body: Buffer };

async function receiver(delayMs = 0) {
  const { createServer } = await import('node:http');
  const uploads: Upload[] = [];
  const server = createServer((request, response) => {
    const chunks: Buffer[] = [];
    request.on('data', (chunk: Buffer) => chunks.push(chunk));
    request.on('end', () => {
      if (request.url?.startsWith('/v1/recordings'))
        uploads.push({
          url: request.url,
          headers: request.headers,
          body: Buffer.concat(chunks),
        });
      setTimeout(() => response.writeHead(200).end(), delayMs);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as { port: number };
  return {
    uploads,
    endpoint: `http://127.0.0.1:${port}/v1/traces`,
    close: () => {
      server.closeAllConnections();
      server.close();
    },
  };
}

async function recordedCall(
  options: InitOptions,
  report: { audioRecordingPath?: string; audioRecordingStartedAt?: number },
) {
  const { init } = await import('@/runtime/init');
  const { attachLiveKit } = await import('@/auto/livekit');
  init(options);
  attachLiveKit({ telemetry });
  const callbacks: (() => Promise<void>)[] = [];
  const ctx = {
    job: { room: { sid: 'RM_room' } },
    makeSessionReport: () => report,
    addShutdownCallback: (callback: () => Promise<void>) =>
      callbacks.push(callback),
  };
  class AgentSession {
    async start() {
      return 'started';
    }
  }
  attachLiveKit({ getJobContext: () => ctx });
  attachLiveKit({ AgentSession });
  const session = new AgentSession();
  expect(await session.start()).toBe('started');
  await session.start();
  return callbacks;
}

async function recordingFile() {
  const { mkdtemp, writeFile } = await import('node:fs/promises');
  const { tmpdir } = await import('node:os');
  const { join } = await import('node:path');
  const path = join(await mkdtemp(join(tmpdir(), 'lk-')), 'audio.ogg');
  await writeFile(path, 'OggS-call-audio');
  return path;
}

it('uploads the call recording once per job at shutdown', async () => {
  const edge = await receiver();
  try {
    const callbacks = await recordedCall(
      { endpoint: edge.endpoint, apiKey: 'key' },
      {
        audioRecordingPath: await recordingFile(),
        audioRecordingStartedAt: 1788652800500,
      },
    );
    expect(callbacks).toHaveLength(1);
    await callbacks[0]!();
    expect(edge.uploads).toHaveLength(1);
    const [upload] = edge.uploads;
    expect(upload!.url).toBe(
      '/v1/recordings?threadId=RM_room&startedAt=1788652800500',
    );
    expect(upload!.headers['x-confident-api-key']).toBe('key');
    expect(upload!.headers['content-type']).toBe('audio/ogg');
    expect(upload!.body.toString()).toBe('OggS-call-audio');
  } finally {
    edge.close();
  }
});

it('skips the upload when the call was not recorded', async () => {
  const edge = await receiver();
  try {
    const callbacks = await recordedCall(
      { endpoint: edge.endpoint, apiKey: 'key' },
      {},
    );
    await callbacks[0]!();
    expect(edge.uploads).toEqual([]);
  } finally {
    edge.close();
  }
});

it('skips the upload when content capture is off', async () => {
  const edge = await receiver();
  try {
    const callbacks = await recordedCall(
      { endpoint: edge.endpoint, apiKey: 'key', captureContent: false },
      {
        audioRecordingPath: await recordingFile(),
        audioRecordingStartedAt: 1788652800500,
      },
    );
    await callbacks[0]!();
    expect(edge.uploads).toEqual([]);
  } finally {
    edge.close();
  }
});

it('bounds a hung call recording upload', async () => {
  const edge = await receiver(10_000);
  try {
    const callbacks = await recordedCall(
      { endpoint: edge.endpoint, apiKey: 'key' },
      {
        audioRecordingPath: await recordingFile(),
        audioRecordingStartedAt: 1788652800500,
      },
    );
    const started = Date.now();
    await callbacks[0]!();
    expect(Date.now() - started).toBeLessThan(7000);
  } finally {
    edge.close();
  }
}, 15_000);
