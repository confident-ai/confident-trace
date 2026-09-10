import type * as PublicApi from '@/index';
import {
  context,
  trace,
  propagation,
  SpanStatusCode,
} from '@opentelemetry/api';
import {
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { SpanOptions } from '@/spans/index';

let api: typeof PublicApi;
let exporter: InMemorySpanExporter;
beforeEach(async () => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  vi.stubEnv('OTEL_SDK_DISABLED', 'false');
  api = await import('@/index');
  exporter = new InMemorySpanExporter();
  api.init({ exporter });
});
afterEach(async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
async function rows() {
  await api.flush();
  return Object.fromEntries(
    exporter.getFinishedSpans().map((s) => [s.name, s]),
  );
}
it('preserves sync returns, this, asynchronous values and local versus trace fields', async () => {
  const child = api.span(
    { name: 'child', type: 'tool', metadata: { local: 1 } },
    function (this: { base: number }, x: number) {
      api.updateSpan({ input: x, output: null });
      api.updateTrace({ input: '', output: [], tags: [], userId: 'u' });
      return this.base + x;
    },
  );
  const value = await api.withSpan({ name: 'root', type: 'agent' }, async () =>
    child.call({ base: 2 }, 3),
  );
  expect(value).toBe(5);
  const r = await rows();
  expect(r.child!.attributes).toMatchObject({
    'confident.span.type': 'tool',
    'gen_ai.operation.name': 'execute_tool',
    'confident.span.input': '3',
    'confident.span.output': 'null',
    'confident.span.metadata': '{"local":1}',
  });
  expect(r.root!.attributes).toMatchObject({
    'confident.trace.input': '""',
    'confident.trace.output': '[]',
    'confident.trace.tags': [],
    'confident.trace.user_id': 'u',
    'confident.span.output': '5',
  });
  expect(r.child!.parentSpanContext?.spanId).toBe(r.root!.spanContext().spanId);
  expect(r.child!.attributes['confident.trace.output']).toBeUndefined();
});
it('preserves option defaults and runtime overrides including undefined and empty values', async () => {
  const fn = api.span(
    {
      name: 'defaults',
      input: null,
      output: '',
      attributes: { 'confident.trace.name': 'trace', 'confident.extra': 7 },
    },
    (x: number) => {
      api.updateSpan({ output: undefined, metadata: { first: 1 } });
      api.updateSpan({ metadata: {}, name: 'renamed' });
      return x;
    },
  );
  expect(fn(5)).toBe(5);
  const r = await rows();
  expect(r.renamed!.attributes).toMatchObject({
    'confident.span.input': 'null',
    'confident.span.output': '""',
    'confident.span.metadata': '{}',
    'confident.trace.name': 'trace',
    'confident.extra': 7,
  });
  const direct = api.span(
    { name: 'raw', attributes: { 'confident.span.type': 'retriever' } },
    () => {
      trace.getActiveSpan()!.setAttribute('confident.span.output', '"manual"');
      return 'automatic';
    },
  );
  direct();
  expect((await rows()).raw!.attributes).toMatchObject({
    'confident.span.type': 'retriever',
    'confident.span.output': '"manual"',
  });
});
it('isolates parallel roots and targets ordinary OTel spans', async () => {
  await Promise.all(
    ['a', 'b'].map((name) =>
      api.withSpan({ name }, async () => {
        await Promise.resolve();
        api.updateTrace({ userId: name });
        trace.getTracer('external').startActiveSpan(`${name}-external`, (s) => {
          api.updateSpan({ expectedOutput: name });
          api.updateTrace({ retrievalContext: [name] });
          s.end();
        });
      }),
    ),
  );
  const r = await rows();
  for (const name of ['a', 'b']) {
    expect(r[name]!.attributes['confident.trace.user_id']).toBe(name);
    expect(
      r[`${name}-external`]!.attributes['confident.span.expected_output'],
    ).toBe(JSON.stringify(name));
    expect(r[name]!.attributes['confident.trace.retrieval_context']).toBe(
      JSON.stringify([name]),
    );
  }
});
it('turns create independent traces and link previous contexts', async () => {
  api.withSpan({ name: 'parent' }, (parent) =>
    api.turn(
      {
        threadId: 'chat',
        turnId: '2',
        previous: parent.spanContext(),
        input: 'question',
      },
      () => {
        api.updateTrace({ output: null });
      },
    ),
  );
  const r = await rows();
  expect(r['agent turn']!.parentSpanContext).toBeUndefined();
  expect(r['agent turn']!.links[0]!.context).toEqual(r.parent!.spanContext());
  expect(r['agent turn']!.attributes['confident.span.input']).toBeUndefined();
  expect(r['agent turn']!.attributes['confident.trace.input']).toBe(
    '"question"',
  );
});
it('records errors without exposing messages and preserves thrown values', async () => {
  for (const error of [new Error('private'), undefined, null, 'private']) {
    const fn = api.span({ name: 'failure' }, () => {
      throw error;
    });
    try {
      fn();
      expect.unreachable();
    } catch (caught) {
      expect(caught).toBe(error);
    }
    await expect(
      api.withSpan({ name: 'async failure' }, async () => {
        throw error;
      }),
    ).rejects.toBe(error);
  }
  for (const s of Object.values(await rows())) {
    expect(s.status.code).toBe(SpanStatusCode.ERROR);
    expect(JSON.stringify(s.attributes)).not.toContain('private');
  }
});
it('starts generators lazily with creation parent and restores consumer context', async () => {
  const produce = api.span(
    { name: 'producer', type: 'tool' },
    function* (): Generator<number, number, number> {
      api.withSpan({ name: 'first' }, () => 1);
      const sent = yield 1;
      api.updateSpan({ output: null });
      api.updateTrace({ output: [] });
      return sent;
    },
  );
  const iterator = api.withSpan({ name: 'creator' }, () => produce());
  expect(Object.keys(await rows())).toEqual(['creator']);
  api.withSpan({ name: 'consumer' }, (current) => {
    expect(iterator.next()).toEqual({ done: false, value: 1 });
    expect(trace.getActiveSpan()).toBe(current);
    expect(iterator.next(7)).toEqual({ done: true, value: 7 });
    expect(trace.getActiveSpan()).toBe(current);
  });
  const r = await rows();
  expect(r.producer!.parentSpanContext?.spanId).toBe(
    r.creator!.spanContext().spanId,
  );
  expect(r.first!.parentSpanContext?.spanId).toBe(
    r.producer!.spanContext().spanId,
  );
  expect(r.producer!.attributes['confident.span.output']).toBe('null');
  expect(r.creator!.attributes['confident.trace.output']).not.toBe('[]');
});
it('handles unstarted close, early close cleanup, recoverable throw and generator returns', async () => {
  const produce = api.span({ name: 'generator' }, function* () {
    try {
      yield 1;
    } catch {
      yield 2;
    } finally {
      api.withSpan({ name: 'cleanup' }, () => undefined);
    }
    return 3;
  });
  const unstarted = produce();
  unstarted.return(9);
  unstarted.next();
  expect(Object.keys(await rows())).toEqual([]);
  const a = produce();
  a.next();
  expect(a.throw(new Error('recoverable'))).toEqual({ done: false, value: 2 });
  expect(Object.keys(await rows())).toEqual([]);
  expect(a.next()).toEqual({ done: true, value: 3 });
  const b = produce();
  b.next();
  b.return(4);
  const r = await rows();
  expect(r.cleanup!.parentSpanContext?.spanId).toBe(
    r.generator!.spanContext().spanId,
  );
  expect(r.generator!.attributes['confident.span.output']).toBe('4');
});
it('queues async generator resumes and isolates interleaved streams', async () => {
  const produce = api.span({ name: 'stream' }, async function* (name: string) {
    await Promise.resolve();
    api.withSpan({ name }, () => 1);
    yield name;
    api.updateSpan({ output: [] });
    yield `${name}-2`;
  });
  const a = produce('a');
  const b = produce('b');
  expect(await Promise.all([a.next(), a.next(), b.next()])).toEqual([
    { done: false, value: 'a' },
    { done: false, value: 'a-2' },
    { done: false, value: 'b' },
  ]);
  await a.return();
  await b.return();
  const r = await rows();
  expect(r.a!.spanContext().traceId).not.toBe(r.b!.spanContext().traceId);
  expect(trace.getActiveSpan()).toBeUndefined();
});
it('keeps ordinary iterator factories unchanged and supports explicit consumption scopes', async () => {
  const iterator = [1, 2][Symbol.iterator]();
  const fn = api.span({ name: 'factory' }, () => iterator);
  expect(fn()).toBe(iterator);
  const r = await rows();
  expect(r.factory).toBeDefined();
  await api.withSpan({ name: 'consume' }, async () => {
    for (const value of iterator) api.updateSpan({ output: value });
  });
  expect((await rows()).consume!.attributes['confident.span.output']).toBe('2');
});
it('respects content policy and does not replace a rejected explicit output', async () => {
  const fn = api.span(
    {
      name: 'redacted',
      redact: (value) => {
        if (value === 'explicit') throw new Error();
        return value;
      },
    },
    () => {
      api.updateSpan({ output: 'explicit' });
      api.updateTrace({ output: 'explicit' });
      return 'fallback';
    },
  );
  fn();
  const r = await rows();
  expect(r.redacted!.attributes['confident.span.output']).toBeUndefined();
  expect(r.redacted!.attributes['confident.trace.output']).toBeUndefined();
  api.withSpan({ name: 'manual', captureContent: false }, () => {
    api.updateSpan({ output: null });
    return 9;
  });
  expect((await rows()).manual!.attributes['confident.span.output']).toBe(
    'null',
  );
});
it('fails open for tracer failures, no-ops when disabled and rejects bad configuration', async () => {
  const tracer = {
    startSpan() {
      throw new Error('exporter');
    },
  } as unknown as NonNullable<SpanOptions['tracer']>;
  expect(api.span({ tracer }, () => 3)()).toBe(3);
  for (const type of ['step', 'workflow', 'typo'])
    expect(() => api.span({ type } as SpanOptions, () => 1)).toThrow();
  expect(() => api.updateSpan({ ouput: 3 } as never)).toThrow();
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  const produce = api.span({}, function* () {
    yield 1;
  });
  expect(produce().constructor.name).not.toBe('Object');
  expect(api.withSpan({}, () => 3)).toBe(3);
  expect(Object.keys(await rows())).toEqual([]);
});
it('works with an application-owned provider and explicit tracer', async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  const other = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(other)],
  });
  provider.register();
  try {
    api.withSpan(
      { name: 'external', tracer: provider.getTracer('owned') },
      () => {
        api.updateSpan({ output: 1 });
      },
    );
    expect(
      other.getFinishedSpans()[0]!.attributes['confident.span.output'],
    ).toBe('1');
  } finally {
    await provider.shutdown();
  }
});

it('runs async generator cleanup under its span on early break and terminal failure', async () => {
  const error = new Error('private');
  const produce = api.span({ name: 'cleanup-stream' }, async function* () {
    try {
      yield 1;
      yield 2;
    } finally {
      await api.withSpan({ name: 'async-cleanup' }, async () => {
        await Promise.resolve();
      });
    }
  });
  for await (const n of produce()) {
    expect(n).toBe(1);
    break;
  }
  const fail = api.span({ name: 'failed-stream' }, async function* () {
    yield 1;
    throw error;
  });
  const stream = fail();
  await stream.next();
  await expect(stream.next()).rejects.toBe(error);
  const r = await rows();
  expect(r['async-cleanup']!.parentSpanContext?.spanId).toBe(
    r['cleanup-stream']!.spanContext().spanId,
  );
  expect(r['failed-stream']!.status.code).toBe(SpanStatusCode.ERROR);
});
it('leaves a generator open when its finally block yields during return', async () => {
  const produce = api.span({ name: 'cleanup-yield' }, function* () {
    try {
      yield 1;
    } finally {
      yield 2;
    }
    return 3;
  });
  const iterator = produce();
  iterator.next();
  expect(iterator.return(9)).toEqual({ done: false, value: 2 });
  expect(Object.keys(await rows())).toEqual([]);
  expect(iterator.next()).toEqual({ done: true, value: 9 });
  expect(
    (await rows())['cleanup-yield']!.attributes['confident.span.output'],
  ).toBe('9');
});
it('does not end a generator when it catches a reentrant next error', async () => {
  const produce = api.span({ name: 'reentrant' }, function* () {
    expect(() => iterator.next()).toThrow('already running');
    yield 1;
    yield 2;
  });
  const iterator = produce();
  iterator.next();
  expect(Object.keys(await rows())).toEqual([]);
  iterator.return();
  expect((await rows()).reentrant!.status.code).not.toBe(SpanStatusCode.ERROR);
});
it('inherits redaction and honors a global opt-out even for explicit helper fields', async () => {
  api.withSpan({ name: 'redactor', redact: () => 'redacted' }, () => {
    api.withSpan({ name: 'inherited' }, () => {
      api.updateSpan({ retrievalContext: ['secret'] });
      return 'secret';
    });
  });
  expect(
    (await rows()).inherited!.attributes['confident.span.retrieval_context'],
  ).toBe('"redacted"');
  await api.shutdown();
  trace.disable();
  context.disable();
  vi.resetModules();
  api = await import('@/index');
  exporter = new InMemorySpanExporter();
  api.init({ exporter, captureContent: false });
  api.withSpan(
    { name: 'disabled-content', captureContent: true, metadata: { secret: 1 } },
    () => {
      api.updateSpan({ output: 'secret' });
      api.updateTrace({ expectedOutput: 'secret' });
      return 'secret';
    },
  );
  expect((await rows())['disabled-content']!.attributes).toEqual({
    'confident.span.type': 'custom',
    'confident.trace.name': 'disabled-content',
  });
});

it('supports metric collection scopes and evaluation IDs', async () => {
  api.traceContext({ metricCollection: 'trace-checks', testCaseId: 'case-1', turnId: 'turn-1' }, () => {
    api.withSpan({ name: 'root', metricCollection: 'root-checks', captureContent: false }, () => {
      api.traceContext({ metricCollection: 'ignored' }, () => {
        api.withSpan({ name: 'child', metricCollection: 'child-checks' }, () => {
          api.updateSpan({ metricCollection: 'updated-child' });
        });
      });
    });
  });
  api.turn({ name: 'turn', threadId: 'chat', metricCollection: 'turn-checks' }, () => {
    api.updateTrace({ testCaseId: 'case-2', turnId: 'turn-2', metricCollection: 'updated-turn' });
  });
  const result = await rows();
  expect(result.root!.attributes).toMatchObject({
    'confident.trace.metric_collection': 'trace-checks',
    'confident.span.metric_collection': 'root-checks',
    'confident.trace.test_case_id': 'case-1',
    'confident.trace.turn_id': 'turn-1',
  });
  expect(result.child!.attributes['confident.span.metric_collection']).toBe('updated-child');
  expect(result.child!.attributes['confident.trace.metric_collection']).toBeUndefined();
  expect(result.turn!.attributes).toMatchObject({
    'confident.trace.metric_collection': 'updated-turn',
    'confident.trace.test_case_id': 'case-2',
    'confident.trace.turn_id': 'turn-2',
  });
});
