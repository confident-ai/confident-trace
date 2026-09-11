import { readFileSync } from 'node:fs';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import type { Attributes } from '@opentelemetry/api';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createSpanProcessor } from '@/runtime/processor';
import * as semconv from '@/semconv/generated';
import { attributes, grpcReceiver, httpReceiver } from '@test/support/otlp';

interface Vector {
  name: string;
  attributes: Attributes;
  events: { name: string; attributes: Attributes }[];
}
const vectors = JSON.parse(
  readFileSync(
    new URL('../../../spec/genai-vectors.json', import.meta.url),
    'utf8',
  ),
) as { emitted_semconv: string; cases: Vector[] };
const manifest = JSON.parse(
  readFileSync(new URL('../../../spec/semconv.json', import.meta.url), 'utf8'),
) as {
  semconvVersion: string;
  schemaUrl: string;
  attributes: Record<string, string>;
  integrations: Record<string, string>;
};
beforeEach(() => {
  for (const key of Object.keys(process.env))
    if (key.startsWith('OTEL_') || key.startsWith('CONFIDENT_'))
      vi.stubEnv(key, undefined);
});
afterEach(() => vi.unstubAllEnvs());
it('native constants match the shared manifest and wire convention version', () => {
  expect(semconv.SEMCONV_VERSION).toBe(manifest.semconvVersion);
  expect(vectors.emitted_semconv).toBe(semconv.SEMCONV_VERSION);
  expect(semconv.SCHEMA_URL).toBe(manifest.schemaUrl);
  expect(semconv.INTEGRATIONS).toEqual(manifest.integrations);
  expect(semconv).toMatchObject(manifest.attributes);
});
it('HTTP/protobuf preserves every shared attribute and legacy event unchanged', async () => {
  const receiver = await httpReceiver();
  vi.stubEnv('CONFIDENT_OTEL_ENDPOINT', `${receiver.url}/collector/v1/traces`);
  vi.stubEnv('OTEL_EXPORTER_OTLP_HEADERS', 'environment=present');
  const provider = new NodeTracerProvider({
    spanProcessors: [
      createSpanProcessor({ apiKey: 'test-key', compression: 'gzip' }),
    ],
  });
  try {
    const tracer = provider.getTracer('third-party', '1.2.3', {
      schemaUrl: semconv.SCHEMA_URL,
    });
    for (const example of vectors.cases) {
      const span = tracer.startSpan(example.name, {
        attributes: example.attributes,
      });
      for (const event of example.events)
        span.addEvent(event.name, event.attributes);
      span.end();
    }
    await provider.forceFlush();
    expect(receiver.received).toHaveLength(1);
    const request = receiver.received[0]!;
    expect(request.url).toBe('/collector/v1/traces');
    expect(request.headers).toMatchObject({
      'x-confident-api-key': 'test-key',
      environment: 'present',
      'content-encoding': 'gzip',
    });
    const scopes = request.body.resourceSpans.flatMap(
      (resource) => resource.scopeSpans,
    );
    expect(scopes[0]?.schemaUrl).toBe(semconv.SCHEMA_URL);
    const spans = scopes.flatMap((scope) => scope.spans);
    expect(spans.map((span) => span.name)).toEqual(
      vectors.cases.map((example) => example.name),
    );
    for (const [index, span] of spans.entries()) {
      expect(attributes(span.attributes)).toEqual(
        vectors.cases[index]!.attributes,
      );
      expect(
        (span.events ?? []).map((event) => ({
          name: event.name,
          attributes: attributes(event.attributes),
        })),
      ).toEqual(vectors.cases[index]!.events);
    }
  } finally {
    await provider.shutdown();
    await receiver.close();
  }
});
it('Confident and explicit HTTP endpoints are complete paths, with explicit headers winning', async () => {
  const receiver = await httpReceiver();
  vi.stubEnv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://invalid.invalid');
  vi.stubEnv('CONFIDENT_OTEL_ENDPOINT', `${receiver.url}/traces-env`);
  vi.stubEnv(
    'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
    'x-confident-api-key=environment',
  );
  for (const endpoint of [undefined, `${receiver.url}/explicit`]) {
    const provider = new NodeTracerProvider({
      spanProcessors: [
        createSpanProcessor({
          ...(endpoint ? { endpoint } : {}),
          apiKey: 'key',
          headers: { 'X-Confident-Api-Key': 'explicit' },
        }),
      ],
    });
    try {
      provider.getTracer('app').startSpan('test').end();
      await provider.forceFlush();
    } finally {
      await provider.shutdown();
    }
  }
  try {
    expect(receiver.received.map((request) => request.url)).toEqual([
      '/traces-env',
      '/explicit',
    ]);
    expect(
      receiver.received.every(
        (request) => request.headers['x-confident-api-key'] === 'explicit',
      ),
    ).toBe(true);
  } finally {
    await receiver.close();
  }
});
it('gRPC sends standard OTLP with explicit authentication metadata', async () => {
  const receiver = await grpcReceiver();
  const provider = new NodeTracerProvider({
    spanProcessors: [
      createSpanProcessor({
        protocol: 'grpc',
        endpoint: receiver.url,
        apiKey: 'test-grpc',
        compression: 'gzip',
      }),
    ],
  });
  try {
    provider
      .getTracer('app')
      .startSpan('grpc', { attributes: { untouched: 'yes', list: ['a', 'b'] } })
      .end();
    await provider.forceFlush();
    expect(receiver.received).toHaveLength(1);
    expect(receiver.received[0]?.headers['x-confident-api-key']).toBe(
      'test-grpc',
    );
    const span =
      receiver.received[0]?.body.resourceSpans[0]?.scopeSpans[0]?.spans[0];
    expect(span?.name).toBe('grpc');
    expect(attributes(span?.attributes)).toEqual({
      untouched: 'yes',
      list: ['a', 'b'],
    });
  } finally {
    await provider.shutdown();
    await receiver.close();
  }
});
