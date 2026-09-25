import { ExportResultCode } from '@opentelemetry/core';
import type { ExportResult } from '@opentelemetry/core';
import type { ReadableSpan, SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { AttributeValue } from '@opentelemetry/api';

// The Confident collector rejects bodies over 32 MiB, and a rejected body is a
// 413, which the OTLP exporter does not retry. Leave room for protobuf framing
// and the resource and scope repeated on every request.
export const MAX_EXPORT_BYTES = 24 * 1024 * 1024;
// Identifiers, timestamps, status and the span's share of the enclosing message.
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

/** Approximate the bytes this span contributes to an OTLP request body.
 *
 * Attributes dominate, because that is where media payloads live. Precision
 * below that does not change which spans end up batched together.
 */
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

/** Split spans into runs that each fit the limit, preserving their order.
 *
 * A span larger than the limit on its own is still returned alone: there is
 * nothing left to split, which is why media carries a per-attribute budget.
 */
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

/** Export in chunks the collector will accept, rather than one oversized body.
 *
 * The OTLP exporter retries only timeouts and 5xx, so a body rejected as too
 * large is dropped outright — taking every span batched alongside it, media or
 * not. Splitting first keeps one large span from costing the rest.
 */
export class BoundedSpanExporter implements SpanExporter {
  constructor(
    private readonly exporter: SpanExporter,
    private readonly maxBytes: number = MAX_EXPORT_BYTES,
  ) {}

  export(
    spans: ReadableSpan[],
    resultCallback: (result: ExportResult) => void,
  ): void {
    const chunks = batches(spans, this.maxBytes);
    if (!chunks.length) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }
    let pending = chunks.length;
    let failure: ExportResult | undefined;
    for (const chunk of chunks)
      this.exporter.export(chunk, (result) => {
        if (result.code !== ExportResultCode.SUCCESS) failure ??= result;
        if (--pending === 0)
          resultCallback(failure ?? { code: ExportResultCode.SUCCESS });
      });
  }

  forceFlush(): Promise<void> {
    return this.exporter.forceFlush?.() ?? Promise.resolve();
  }

  shutdown(): Promise<void> {
    return this.exporter.shutdown();
  }
}
