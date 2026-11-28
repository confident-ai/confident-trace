import { expect, test, vi } from 'vitest';
import { context, propagation, trace } from '@opentelemetry/api';
import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import {
  BasicTracerProvider,
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import { OTLPTraceExporter as GrpcTraceExporter } from '@opentelemetry/exporter-trace-otlp-grpc';
import { BoundedSpanExporter, spanSize } from '@/exporters/bounded';
import { grpcReceiver, httpReceiver } from '@test/support/otlp';

// ----- Offline fixture helpers -----
async function recordedSpans(count: number): Promise<ReadableSpan[]> {
  const memory = new InMemorySpanExporter();
  const provider = new BasicTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(memory)],
  });
  const tracer = provider.getTracer('bounded-concurrency-test');
  for (let i = 0; i < count; i++) {
    const span = tracer.startSpan(`span-${i}`);
    span.setAttribute('gen_ai.input.messages', 'x'.repeat(1024));
    span.end();
  }
  const spans = [...memory.getFinishedSpans()];
  await provider.shutdown();
  return spans;
}

function exported(exporter: SpanExporter, spans: ReadableSpan[]) {
  return new Promise<ExportResult>((resolve) =>
    exporter.export(spans, resolve),
  );
}
// ----- End offline fixture helpers -----

test.each(['http', 'grpc'] as const)(
  'split requests respect the default %s OTLP concurrency limit',
  async (protocol) => {
    const receiver = await (protocol === 'http'
      ? httpReceiver()
      : grpcReceiver());
    const spans = await recordedSpans(33);
    // Scale the byte threshold, keeping the real transport's default limit of 30.
    const exporter = new BoundedSpanExporter(
      protocol === 'http'
        ? new OTLPTraceExporter({ url: `${receiver.url}/v1/traces` })
        : new GrpcTraceExporter({ url: receiver.url }),
      spanSize(spans[0]!) + 16,
    );
    try {
      const result = await exported(exporter, spans);
      expect(result).toEqual({ code: ExportResultCode.SUCCESS });
      expect(
        receiver.received.flatMap(({ body }) =>
          body.resourceSpans.flatMap((resource) =>
            resource.scopeSpans.flatMap((scope) =>
              scope.spans.map((span) => span.name),
            ),
          ),
        ),
      ).toEqual(spans.map((span) => span.name));
    } finally {
      await exporter.shutdown();
      await receiver.close();
    }
  },
);

test.each(['forceFlush', 'shutdown'] as const)(
  '%s waits for unsent chunks',
  async (method) => {
    const receiver = await httpReceiver();
    const spans = await recordedSpans(4);
    const exporter = new BoundedSpanExporter(
      new OTLPTraceExporter({
        url: `${receiver.url}/v1/traces`,
        concurrencyLimit: 1,
      }),
      spanSize(spans[0]!) + 16,
      1,
    );
    try {
      const result = exported(exporter, spans);
      await exporter[method]();
      expect(receiver.received).toHaveLength(spans.length);
      expect(await result).toEqual({ code: ExportResultCode.SUCCESS });
    } finally {
      await exporter.shutdown();
      await receiver.close();
    }
  },
);

test('overlapping exports share the wrapped exporter capacity', async () => {
  const receiver = await httpReceiver();
  const spans = await recordedSpans(6);
  const exporter = new BoundedSpanExporter(
    new OTLPTraceExporter({
      url: `${receiver.url}/v1/traces`,
      concurrencyLimit: 1,
    }),
    spanSize(spans[0]!) + 16,
    1,
  );
  try {
    const results = await Promise.all([
      exported(exporter, spans.slice(0, 3)),
      exported(exporter, spans.slice(3)),
    ]);
    expect(results).toEqual([
      { code: ExportResultCode.SUCCESS },
      { code: ExportResultCode.SUCCESS },
    ]);
    expect(receiver.received).toHaveLength(spans.length);
  } finally {
    await exporter.shutdown();
    await receiver.close();
  }
});

test('public flush exports a configured small-batch backlog without local drops', async () => {
  const receiver = await httpReceiver();
  vi.stubEnv('OTEL_BSP_MAX_EXPORT_BATCH_SIZE', '1');
  vi.stubEnv('OTEL_SDK_DISABLED', 'false');
  const api = await import('@/index');
  try {
    api.init({ endpoint: `${receiver.url}/v1/traces`, instrumentations: [] });
    const work = api.span({ name: 'small-batch-work' }, (i: number) => i);
    for (let i = 0; i < 33; i++) work(i);
    const flushed = await api.flush();
    await api.shutdown();
    const received = receiver.received.flatMap(({ body }) =>
      body.resourceSpans.flatMap((resource) =>
        resource.scopeSpans.flatMap((scope) => scope.spans),
      ),
    );
    console.info('Public small-batch runtime result', {
      requested: 33,
      received: received.length,
      flushed,
    });
    expect(flushed).toBe(true);
    expect(received).toHaveLength(33);
  } finally {
    await api.shutdown();
    trace.disable();
    context.disable();
    propagation.disable();
    vi.unstubAllEnvs();
    await receiver.close();
  }
});
