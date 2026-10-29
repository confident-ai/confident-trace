import { describe, expect, test } from 'vitest';
import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import { BoundedSpanExporter, batches, spanSize } from '@/exporters/bounded';

// ----- Offline fixture helpers -----
/** A SpanExporter that remembers the shape of every request it was given. */
class Recorder implements SpanExporter {
  readonly requests: ReadableSpan[][] = [];
  flushed = false;
  stopped = false;

  constructor(private readonly failOver?: number) {}

  export(
    spans: ReadableSpan[],
    resultCallback: (result: ExportResult) => void,
  ): void {
    this.requests.push(spans);
    resultCallback({
      code:
        this.failOver !== undefined && spans.length > this.failOver
          ? ExportResultCode.FAILED
          : ExportResultCode.SUCCESS,
    });
  }

  async forceFlush(): Promise<void> {
    this.flushed = true;
  }

  async shutdown(): Promise<void> {
    this.stopped = true;
  }
}

function spanOf(payloadBytes: number): ReadableSpan {
  return {
    name: 'span',
    attributes: { 'gen_ai.input.messages': 'x'.repeat(payloadBytes) },
    events: [],
  } as unknown as ReadableSpan;
}

function exported(exporter: BoundedSpanExporter, spans: ReadableSpan[]) {
  return new Promise<ExportResult>((resolve) =>
    exporter.export(spans, resolve),
  );
}

// ----- End offline fixture helpers -----

describe('size estimation', () => {
  test('grows with the payload it carries', () => {
    expect(spanSize(spanOf(10_000)) - spanSize(spanOf(10))).toBe(9_990);
  });
});

describe('splitting', () => {
  test('a batch that fits is sent as one request', () => {
    const spans = [spanOf(1_000), spanOf(1_000)];
    expect(batches(spans, 1_000_000)).toEqual([spans]);
  });

  test('an oversized batch is split in order', () => {
    const spans = Array.from({ length: 6 }, () => spanOf(10_000));
    const result = batches(spans, spanSize(spanOf(10_000)) * 2);
    expect(result.map((batch) => batch.length)).toEqual([2, 2, 2]);
    expect(result.flat()).toEqual(spans);
  });

  test('a span too large alone is still sent alone', () => {
    const spans = [spanOf(10), spanOf(500_000), spanOf(10)];
    const result = batches(spans, 1_000);
    expect(result.map((batch) => batch.length)).toEqual([1, 1, 1]);
    expect(result[1]).toEqual([spans[1]]);
  });
});

describe('the exporter', () => {
  test('splits what the collector would reject', async () => {
    const recorder = new Recorder();
    const exporter = new BoundedSpanExporter(
      recorder,
      spanSize(spanOf(10)) * 2,
    );
    const spans = Array.from({ length: 5 }, () => spanOf(10));
    expect(await exported(exporter, spans)).toEqual({
      code: ExportResultCode.SUCCESS,
    });
    expect(recorder.requests.map((request) => request.length)).toEqual([
      2, 2, 1,
    ]);
  });

  test('one failed request fails the export', async () => {
    const recorder = new Recorder(1);
    const exporter = new BoundedSpanExporter(
      recorder,
      spanSize(spanOf(10)) * 2,
    );
    const spans = Array.from({ length: 3 }, () => spanOf(10));
    expect((await exported(exporter, spans)).code).toBe(
      ExportResultCode.FAILED,
    );
    // Later batches are still attempted.
    expect(recorder.requests).toHaveLength(2);
  });

  test('an empty export succeeds without a request', async () => {
    const recorder = new Recorder();
    expect(await exported(new BoundedSpanExporter(recorder), [])).toEqual({
      code: ExportResultCode.SUCCESS,
    });
    expect(recorder.requests).toHaveLength(0);
  });

  test('flush and shutdown reach the wrapped exporter', async () => {
    const recorder = new Recorder();
    const exporter = new BoundedSpanExporter(recorder);
    await exporter.forceFlush();
    await exporter.shutdown();
    expect(recorder.flushed && recorder.stopped).toBe(true);
  });

  test('a synchronous exporter exception does not poison later queued exports', async () => {
    const recorder = new Recorder();
    let fail = true;
    const original = recorder.export.bind(recorder);
    recorder.export = (spans, callback) => {
      if (fail) {
        fail = false;
        throw new Error('export failed');
      }
      original(spans, callback);
    };
    const exporter = new BoundedSpanExporter(recorder);
    const failed = exported(exporter, [spanOf(10)]);
    const recovered = exported(exporter, [spanOf(10)]);
    expect(await failed).toMatchObject({
      code: ExportResultCode.FAILED,
      error: new Error('export failed'),
    });
    expect(await recovered).toEqual({ code: ExportResultCode.SUCCESS });
  });

  test('uses the available concurrency without exceeding it across exports', async () => {
    const recorder = new Recorder();
    let active = 0;
    let maximum = 0;
    recorder.export = (spans, callback) => {
      active++;
      maximum = Math.max(maximum, active);
      recorder.requests.push(spans);
      setImmediate(() => {
        active--;
        callback({ code: ExportResultCode.SUCCESS });
      });
    };
    const exporter = new BoundedSpanExporter(recorder, spanSize(spanOf(10)), 2);
    const spans = Array.from({ length: 8 }, () => spanOf(10));
    const results = await Promise.all([
      exported(exporter, spans.slice(0, 4)),
      exported(exporter, spans.slice(4)),
    ]);
    expect(maximum).toBe(2);
    expect(recorder.requests.flat()).toEqual(spans);
    expect(
      results.every((result) => result.code === ExportResultCode.SUCCESS),
    ).toBe(true);
  });

  test.each(['forceFlush', 'shutdown'] as const)(
    '%s preserves lifecycle errors',
    async (method) => {
      const recorder = new Recorder();
      recorder[method] = async () => {
        throw new Error('cleanup failed');
      };
      const exporter = new BoundedSpanExporter(recorder);
      await expect(exporter[method]()).rejects.toThrow('cleanup failed');
    },
  );
});
