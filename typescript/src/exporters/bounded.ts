import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { AttributeValue } from '@opentelemetry/api';

export const MAX_EXPORT_BYTES = 24 * 1024 * 1024;
export const EXPORT_CONCURRENCY = 30;
const SPAN_OVERHEAD = 1024;

function valueSize(value: AttributeValue | undefined): number {
  if (typeof value === 'string') return value.length;
  if (Array.isArray(value))
    return (
      value.reduce<number>(
        (total, item) => total + valueSize(item as AttributeValue),
        0,
      ) +
      2 * value.length
    );
  return 8;
}

export function spanSize(span: ReadableSpan): number {
  let total = SPAN_OVERHEAD + span.name.length;
  for (const [key, value] of Object.entries(span.attributes))
    total += key.length + valueSize(value);
  for (const event of span.events) {
    total += SPAN_OVERHEAD + event.name.length;
    for (const [key, value] of Object.entries(event.attributes ?? {}))
      total += key.length + valueSize(value);
  }
  return total;
}

export function batches(
  spans: ReadableSpan[],
  maxBytes: number,
): ReadableSpan[][] {
  const result: ReadableSpan[][] = [];
  let current: ReadableSpan[] = [];
  let used = 0;
  for (const span of spans) {
    const size = spanSize(span);
    if (current.length && used + size > maxBytes) {
      result.push(current);
      current = [];
      used = 0;
    }
    current.push(span);
    used += size;
  }
  if (current.length) result.push(current);
  return result;
}

export class BoundedSpanExporter implements SpanExporter {
  private pending: Promise<void> = Promise.resolve();
  private readonly waiting = new Set<() => void>();
  private active = 0;

  constructor(
    private readonly exporter: SpanExporter,
    private readonly maxBytes: number = MAX_EXPORT_BYTES,
    private readonly concurrency: number = EXPORT_CONCURRENCY,
  ) {
    if (!Number.isSafeInteger(concurrency) || concurrency < 1)
      throw new RangeError('Export concurrency must be a positive integer');
  }

  private drain(): void {
    while (this.active < this.concurrency && this.waiting.size) {
      const send = this.waiting.values().next().value!;
      this.waiting.delete(send);
      send();
    }
  }

  private request(chunk: ReadableSpan[]): Promise<ExportResult> {
    return new Promise((resolve) => {
      this.waiting.add(() => {
        this.active++;
        let completed = false;
        const complete = (result: ExportResult) => {
          if (completed) return;
          completed = true;
          // OTLP releases its in-flight slot after invoking the callback.
          // Resume on the next turn, also avoiding recursive sync callbacks.
          setImmediate(() => {
            this.active--;
            resolve(result);
            this.drain();
          });
        };
        try {
          this.exporter.export(chunk, complete);
        } catch (error) {
          complete({
            code: ExportResultCode.FAILED,
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      });
      this.drain();
    });
  }

  export(
    spans: ReadableSpan[],
    resultCallback: (result: ExportResult) => void,
  ): void {
    const chunks = batches(spans, this.maxBytes);
    if (!chunks.length) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }
    let failure: ExportResult | undefined;
    const operation = Promise.all(
      chunks.map((chunk) =>
        this.request(chunk).then((result) => {
          if (result.code !== ExportResultCode.SUCCESS) failure ??= result;
        }),
      ),
    );
    // One capacity budget per destination covers overlapping processor flushes.
    this.pending = Promise.all([this.pending, operation]).then(() => {});
    void operation.then(() =>
      resultCallback(failure ?? { code: ExportResultCode.SUCCESS }),
    );
  }

  async forceFlush(): Promise<void> {
    await this.pending;
    await this.exporter.forceFlush?.();
  }

  async shutdown(): Promise<void> {
    await this.pending;
    await this.exporter.shutdown();
  }
}
