import { context, trace, propagation } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type * as PublicApi from '@/index';
import { readFileSync } from 'node:fs';
const V = JSON.parse(
  readFileSync(
    new URL('../../../spec/parity-vectors.json', import.meta.url),
    'utf8',
  ),
);
class RecordingExporter extends InMemorySpanExporter {
  closed = false;
  override shutdown(): Promise<void> {
    this.closed = true;
    return Promise.resolve();
  }
}
let api: typeof PublicApi;
let fallback: InMemorySpanExporter;
let destinations: Map<string, InMemorySpanExporter>;
beforeEach(async () => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  vi.stubEnv('OTEL_SDK_DISABLED', 'false');
  api = await import('@/index');
  fallback = new InMemorySpanExporter();
  destinations = new Map();
  api.init({
    exporter: fallback,
    projectExporterFactory: (key) => {
      const exporter = new RecordingExporter();
      destinations.set(key, exporter);
      return exporter;
    },
  });
});
afterEach(async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
it('records shared manual LLM, thread and linkage fields', async () => {
  const llm = Object.fromEntries(
    Object.entries(V.llm).map(([k, v]) => [
      k.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
      v,
    ]),
  );
  const model = api.span({ type: 'llm', ...llm }, () => {
    api.updateSpan({
      input: 'chosen',
      output: 'explicit',
      expectedOutput: 'gold',
      retrievalContext: ['doc'],
    });
    api.updateTrace({
      thread: V.thread,
      testCaseId: V.test_case_id,
      metadata: { trace: true },
    });
    api.updateLlmSpan({ outputTokenCount: 5 });
    return 'automatic';
  });
  expect(model()).toBe('automatic');
  await api.flush();
  const a = fallback.getFinishedSpans()[0]!.attributes;
  expect(a['confident.span.output']).toBe(JSON.stringify('explicit'));
  expect(a['gen_ai.usage.input_tokens']).toBe(0);
  expect(a['gen_ai.usage.output_tokens']).toBe(5);
  expect(a['confident.llm.cost_per_input_token']).toBe(0);
  expect(a['confident.trace.thread.id']).toBe('chat-42');
  expect(a['confident.trace.thread_id']).toBe('chat-42');
  expect(a['confident.trace.thread.tags']).toEqual(['conversation']);
  expect(JSON.parse(a['confident.trace.thread.metadata'] as string)).toEqual({
    topic: 'support',
  });
  expect(a['confident.trace.test_case_id']).toBe('case-42');
  expect(() => api.updateLlmSpan({ inputTokenCount: -1 })).toThrow();
  expect(() => api.updateLlmSpan({ costPerInputToken: NaN })).toThrow();
  expect(() =>
    api.updateTrace({ threadId: 'a', thread: { id: 'b' } }),
  ).toThrow();
});
it('isolates concurrent projects and exports child spans before parent ends', async () => {
  const request = (key: string) =>
    api.withProject({ apiKey: key }, () =>
      api.withSpan({ name: 'request-' + key }, async () => {
        await Promise.resolve();
        trace
          .getTracer('native-provider')
          .startSpan('model-' + key)
          .end();
        await api.flush();
        expect(
          destinations
            .get(key)!
            .getFinishedSpans()
            .map((s) => s.name),
        ).toEqual(['model-' + key]);
        expect(() => api.withProject({ apiKey: 'other' }, () => {})).toThrow();
      }),
    );
  await Promise.all([request('a'), request('b')]);
  await api.flush();
  expect(fallback.getFinishedSpans()).toHaveLength(0);
  for (const [key, exporter] of destinations)
    expect(exporter.getFinishedSpans().map((s) => s.name)).toEqual([
      'model-' + key,
      'request-' + key,
    ]);
  const late = api.withProject({ apiKey: 'a' }, () =>
    trace.getTracer('native').startSpan('late'),
  );
  late.end();
  await api.flush();
  expect(destinations.get('a')!.getFinishedSpans().at(-1)!.name).toBe('late');
});
it('suppresses undecorated native calls and preserves turn routing', async () => {
  await api.withTracingSuppressed(async () => {
    await Promise.resolve();
    trace.getTracer('native').startSpan('hidden').end();
    api.turn({ thread: V.thread }, () => api.withSpan({}, () => {}));
  });
  await api.withProject({ apiKey: 'a' }, () =>
    api.turn({ thread: V.thread }, async () => {
      await Promise.resolve();
      api.withSpan({ name: 'child' }, () => {});
    }),
  );
  await api.flush();
  expect(fallback.getFinishedSpans()).toHaveLength(0);
  expect(destinations.get('a')!.getFinishedSpans()).toHaveLength(2);
  expect(
    destinations.get('a')!.getFinishedSpans()[0]!.attributes[
      'gen_ai.conversation.id'
    ],
  ).toBe('chat-42');
  expect(() =>
    api.withTracingSuppressed(() => {
      throw new Error('application');
    }),
  ).toThrow('application');
  api.withSpan({ name: 'restored' }, () => {});
  await api.flush();
  expect(fallback.getFinishedSpans()[0]!.name).toBe('restored');
});
it('does not export on updates without active spans', async () => {
  api.updateTrace({ thread: V.thread });
  api.updateLlmSpan({ model: 'none' });
  await api.flush();
  expect(fallback.getFinishedSpans()).toHaveLength(0);
});

it('routes and suppresses instrumented OpenAI calls in undecorated handlers', async () => {
  const { instrumentOpenAI } = await import('@/integrations/openai/index');
  const client = {
    responses: { create: async () => ({}) },
    chat: {
      completions: {
        create: async () => ({
          id: 'fake',
          model: 'gpt-test',
          choices: [
            {
              message: { role: 'assistant', content: 'hello' },
              finish_reason: 'stop',
            },
          ],
          usage: { prompt_tokens: 2, completion_tokens: 1 },
        }),
      },
    },
  };
  const undo = instrumentOpenAI(client);
  try {
    const answer = () => client.chat.completions.create();
    await api.withTracingSuppressed(async () => {
      expect(api.init().active).toBe(true);
      await answer();
    });
    await api.withProject({ apiKey: 'tenant' }, answer);
    await api.flush();
    expect(fallback.getFinishedSpans()).toHaveLength(0);
    expect(destinations.get('tenant')!.getFinishedSpans()).toHaveLength(1);
    expect(
      destinations.get('tenant')!.getFinishedSpans()[0]!.attributes[
        'gen_ai.usage.input_tokens'
      ],
    ).toBe(2);
  } finally {
    undo();
  }
});
it('cleans idle exporters without losing active or delayed spans', async () => {
  const delayed = api.span({}, function* () {
    yield 'ok';
  });
  const generator = api.withProject({ apiKey: 'old' }, delayed);
  const active = api.withProject({ apiKey: 'active' }, () =>
    trace.getTracer('native').startSpan('active'),
  );
  for (let i = 0; i < 70; i++) {
    api.withProject({ apiKey: String(i) }, () => api.withSpan({}, () => {}));
  }
  await api.flush();
  expect([...generator]).toEqual(['ok']);
  active.end();
  await api.flush();
  expect(destinations.get('old')!.getFinishedSpans()).toHaveLength(1);
  expect(destinations.get('active')!.getFinishedSpans()).toHaveLength(1);
  expect(fallback.getFinishedSpans()).toHaveLength(0);
});

it('uses scoped credentials over default auth headers on the wire', async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  api = await import('@/index');
  const { httpReceiver } = await import('@test/support/otlp');
  const receiver = await httpReceiver();
  try {
    api.init({
      endpoint: receiver.url,
      apiKey: 'default',
      headers: { 'X-Confident-Api-Key': 'header-default' },
      instrumentations: [],
    });
    api.withProject({ apiKey: 'default' }, () =>
      api.withSpan({ name: 'first' }, () => {}),
    );
    api.withProject({ apiKey: 'tenant' }, () =>
      api.withSpan({ name: 'second' }, () => {}),
    );
    await api.flush();
    expect(
      new Set(receiver.received.map((r) => r.headers['x-confident-api-key'])),
    ).toEqual(new Set(['default', 'tenant']));
    for (const row of receiver.received)
      expect(JSON.stringify(row.body)).not.toContain('api-key');
    vi.stubEnv('OTEL_SDK_DISABLED', 'true');
    expect(api.withProject({ apiKey: 'disabled' }, () => 7)).toBe(7);
  } finally {
    await api.shutdown();
    await receiver.close();
  }
});

it('updates all fields through one helper and warns once on incompatible spans', async () => {
  const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
  try {
    api.withSpan({ type: 'tool' }, () => {
      api.updateSpan({
        output: 'kept',
        model: 'private-model',
        inputTokenCount: 7,
      });
      api.updateLlmSpan({ outputTokenCount: 2 });
    });
    api.withSpan({ type: 'llm' }, () => {
      api.updateSpan({ output: 'answer', model: 'model', inputTokenCount: 0 });
      api.updateLlmSpan({ outputTokenCount: 2 });
    });
    trace.getTracer('native').startActiveSpan(
      'native',
      {
        attributes: { 'gen_ai.operation.name': 'chat' },
      },
      (current) => {
        api.updateSpan({ model: 'native-model' });
        current.end();
      },
    );
    await api.flush();
    const [tool, llm, native] = fallback.getFinishedSpans();
    expect(
      JSON.parse(tool!.attributes['confident.span.output'] as string),
    ).toBe('kept');
    expect(tool!.attributes['gen_ai.request.model']).toBeUndefined();
    expect(tool!.attributes['gen_ai.usage.output_tokens']).toBeUndefined();
    expect(llm!.attributes['gen_ai.usage.input_tokens']).toBe(0);
    expect(llm!.attributes['gen_ai.usage.output_tokens']).toBe(2);
    expect(native!.attributes['gen_ai.request.model']).toBe('native-model');
    expect(warning).toHaveBeenCalledTimes(1);
    expect(warning.mock.calls[0]![0]).toContain('skipped LLM fields');
    expect(warning.mock.calls[0]![0]).not.toContain('private-model');
  } finally {
    warning.mockRestore();
  }
});

it.each(['agent', 'llm', 'retriever', 'tool', 'custom'] as const)(
  'supports shared update fields on %s spans',
  async (type) => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      const fields = {
        input: 'question',
        output: 'answer',
        metadata: { source: 'test' },
        context: ['reference'],
        retrievalContext: ['document'],
        expectedOutput: 'answer',
        toolsCalled: [{ name: 'lookup' }],
        expectedTools: [{ name: 'lookup' }],
      };
      api.withSpan({ type }, () => {
        api.updateSpan({ ...fields, model: 'test', inputTokenCount: 0 });
        api.updateSpan({ outputTokenCount: 1 });
      });
      await api.flush();
      const attrs = fallback.getFinishedSpans()[0]!.attributes;
      for (const [key, value] of Object.entries(fields)) {
        const snake = key.replace(/[A-Z]/g, (c) => '_' + c.toLowerCase());
        expect(JSON.parse(attrs['confident.span.' + snake] as string)).toEqual(
          value,
        );
      }
      expect('gen_ai.request.model' in attrs).toBe(type === 'llm');
      expect(warning).toHaveBeenCalledTimes(type === 'llm' ? 0 : 1);
    } finally {
      warning.mockRestore();
    }
  },
);
