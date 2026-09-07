import { context, propagation, trace } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createSpanProcessor } from '@/runtime/processor';
import { withinBudget } from '@/runtime/lifecycle';

beforeEach(() => {
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  for (const key of Object.keys(process.env))
    if (key.startsWith('OTEL_') || key === 'CONFIDENT_API_KEY')
      vi.stubEnv(key, undefined);
});
afterEach(async () => {
  const { shutdown } = await import('@/runtime/init');
  await shutdown(1000);
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});
it('exports standard spans, preserves async parentage and merges resources', async () => {
  vi.stubEnv('OTEL_RESOURCE_ATTRIBUTES', 'service.name=environment,custom=env');
  const exporter = new InMemorySpanExporter();
  const { init, flush, shutdown } = await import('@/runtime/init');
  const runtime = init({
    exporter,
    resourceAttributes: { 'service.name': 'explicit' },
  });
  expect(init()).toBe(runtime);
  await runtime.getTracer().startActiveSpan('parent', async (parent) => {
    await Promise.resolve();
    const child = trace.getTracer('third-party').startSpan('child');
    child.end();
    parent.end();
  });
  expect(await flush()).toBe(true);
  const [child, parent] = exporter.getFinishedSpans();
  expect(child?.parentSpanContext?.spanId).toBe(parent?.spanContext().spanId);
  expect(parent?.resource.attributes).toMatchObject({
    'service.name': 'explicit',
    custom: 'env',
  });
  expect(parent?.instrumentationScope.schemaUrl).toBe(
    'https://opentelemetry.io/schemas/1.37.0',
  );
  expect(await shutdown()).toBe(true);
  expect(runtime.active).toBe(false);
  expect(init()).toBe(runtime);
  expect(runtime.getTracer().startSpan('after shutdown').isRecording()).toBe(
    false,
  );
});
it('preserves isolation between concurrent asynchronous roots', async () => {
  const exporter = new InMemorySpanExporter();
  const { init } = await import('@/runtime/init');
  const runtime = init({ exporter });
  await Promise.all(
    ['a', 'b'].map((name) =>
      runtime.getTracer().startActiveSpan(name, async (parent) => {
        await new Promise((resolve) => setTimeout(resolve, 1));
        runtime.getTracer().startSpan(`${name}-child`).end();
        parent.end();
      }),
    ),
  );
  await runtime.flush();
  const spans = exporter.getFinishedSpans();
  for (const name of ['a', 'b'])
    expect(
      spans.find((span) => span.name === `${name}-child`)?.parentSpanContext
        ?.spanId,
    ).toBe(spans.find((span) => span.name === name)?.spanContext().spanId);
});
it('factory works with an application-owned provider and package shutdown leaves it alone', async () => {
  const exporter = new InMemorySpanExporter();
  const other = new InMemorySpanExporter();
  const provider = new NodeTracerProvider({
    spanProcessors: [
      createSpanProcessor({ exporter }),
      createSpanProcessor({ exporter: other }),
    ],
  });
  provider.register();
  const { shutdown } = await import('@/runtime/init');
  await shutdown();
  provider.getTracer('application').startSpan('still alive').end();
  await provider.forceFlush();
  expect(exporter.getFinishedSpans()).toHaveLength(1);
  expect(other.getFinishedSpans()).toHaveLength(1);
  await provider.shutdown();
});
it('registration conflict leaves the existing provider and cleans up the unused exporter', async () => {
  const provider = new NodeTracerProvider();
  provider.register();
  const originalGlobal = trace.getTracerProvider();
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown');
  const { init } = await import('@/runtime/init');
  expect(init({ exporter }).active).toBe(false);
  await vi.waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(trace.getTracerProvider()).toBe(originalGlobal);
  await provider.shutdown();
});
it('disabled and failed initialization do not consume injected exporters', async () => {
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown');
  const { init } = await import('@/runtime/init');
  vi.stubEnv('OTEL_SDK_DISABLED', 'true');
  expect(init({ exporter }).active).toBe(false);
  await createSpanProcessor({ exporter }).shutdown();
  vi.stubEnv('OTEL_SDK_DISABLED', undefined);
  expect(init({ exporter, maxContentBytes: 1 }).active).toBe(false);
  expect(close).not.toHaveBeenCalled();
});
it('bounds waiting and consumes synchronous/asynchronous failures', async () => {
  expect(await withinBudget(() => new Promise(() => {}), 5)).toBe(false);
  expect(
    await withinBudget(() => {
      throw new Error('failure');
    }, 100),
  ).toBe(false);
  expect(
    await withinBudget(() => Promise.reject(new Error('failure')), 100),
  ).toBe(false);
  expect(await withinBudget(() => Promise.resolve(), -1)).toBe(false);
});
it('shutdown closes the gate immediately, is idempotent, and cleanup can finish after timeout', async () => {
  let finish!: () => void;
  const exporter = new InMemorySpanExporter();
  const close = vi.spyOn(exporter, 'shutdown').mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const { init } = await import('@/runtime/init');
  const runtime = init({ exporter });
  expect(await runtime.shutdown(5)).toBe(false);
  expect(runtime.active).toBe(false);
  finish();
  expect(await runtime.shutdown()).toBe(true);
  expect(close).toHaveBeenCalledOnce();
});
