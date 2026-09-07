import { diag } from '@opentelemetry/api';
import type { Context } from '@opentelemetry/api';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import type {
  ReadableSpan,
  Span,
  SpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import { isDisabled, resolveExportOptions } from '@/config/resolve';
import type { ExportOptions } from '@/config/types';
import { createHttpExporter } from '@/exporters/http';
import { createGrpcExporter } from '@/exporters/grpc';

class GatedProcessor implements SpanProcessor {
  private closed = false;
  private closing: Promise<void> | undefined;
  constructor(private readonly delegate?: SpanProcessor) {}
  onStart(span: Span, parentContext: Context): void {
    if (!this.closed) this.delegate?.onStart(span, parentContext);
  }
  onEnd(span: ReadableSpan): void {
    if (!this.closed) {
      try {
        this.delegate?.onEnd(span);
      } catch {
        diag.debug('Confident Trace span processor failed');
      }
    }
  }
  forceFlush(): Promise<void> {
    if (this.closed) return this.closing ?? Promise.resolve();
    return Promise.resolve().then(() => this.delegate?.forceFlush());
  }
  shutdown(): Promise<void> {
    if (!this.closing) {
      this.closed = true;
      this.closing = Promise.resolve().then(() => this.delegate?.shutdown());
    }
    return this.closing;
  }
}

/** Application owns the returned processor and installs it at provider construction. */
export function createSpanProcessor(
  options: ExportOptions = {},
): SpanProcessor {
  if (isDisabled()) return new GatedProcessor();
  let exporter = options.exporter;
  if (!exporter) {
    const resolved = resolveExportOptions(options);
    exporter =
      resolved.protocol === 'grpc'
        ? createGrpcExporter(resolved)
        : createHttpExporter(resolved);
  }
  try {
    return new GatedProcessor(new BatchSpanProcessor(exporter));
  } catch (error) {
    void Promise.resolve()
      .then(() => exporter.shutdown())
      .catch(() => {});
    throw error;
  }
}
