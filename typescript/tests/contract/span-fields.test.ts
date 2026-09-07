import type * as PublicApi from '@/index';
import { readFileSync } from 'node:fs';
import { context, trace, propagation } from '@opentelemetry/api';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { attributes, grpcReceiver, httpReceiver } from '@test/support/otlp';

const vectors = JSON.parse(
  readFileSync(
    new URL('../../../spec/span-field-vectors.json', import.meta.url),
    'utf8',
  ),
) as { cases: { field: string; value: unknown }[] };
let api: typeof PublicApi;
beforeEach(async () => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  for (const key of Object.keys(process.env))
    if (key.startsWith('OTEL_') || key === 'CONFIDENT_API_KEY')
      vi.stubEnv(key, undefined);
  api = await import('@/index');
});
afterEach(async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
it.each(['http/protobuf', 'grpc'] as const)(
  'exports Python-compatible scoped fields over %s',
  async (protocol) => {
    const receiver =
      protocol === 'grpc' ? await grpcReceiver() : await httpReceiver();
    const fields = Object.fromEntries(
      vectors.cases.map((v) => [
        v.field.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase()),
        v.value,
      ]),
    );
    try {
      api.init({ endpoint: receiver.url, protocol, apiKey: 'test' });
      const child = api.span({ name: 'retriever', type: 'retriever' }, () => {
        api.updateSpan(fields);
        api.updateTrace(fields);
        return 'must not replace explicit empty output';
      });
      await api.withSpan({ name: 'agent', type: 'agent' }, async () => child());
      await api.flush();
      const rows = Object.fromEntries(
        receiver.received
          .flatMap((r) =>
            r.body.resourceSpans.flatMap((resource) =>
              resource.scopeSpans.flatMap((scope) => scope.spans),
            ),
          )
          .map((s) => [s.name, attributes(s.attributes)]),
      );
      for (const { field, value } of vectors.cases) {
        expect(
          JSON.parse(rows.retriever![`confident.span.${field}`] as string),
        ).toEqual(value);
        expect(
          JSON.parse(rows.agent![`confident.trace.${field}`] as string),
        ).toEqual(value);
        expect(rows.retriever![`confident.trace.${field}`]).toBeUndefined();
      }
      expect(rows.retriever!['confident.span.type']).toBe('retriever');
    } finally {
      await api.shutdown();
      await receiver.close();
    }
  },
);
