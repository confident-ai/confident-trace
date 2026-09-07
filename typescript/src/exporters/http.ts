import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import type { ResolvedExportOptions } from '@/config/types';

export function createHttpExporter(
  options: ResolvedExportOptions,
): OTLPTraceExporter {
  return new OTLPTraceExporter({
    headers: options.headers,
    ...(options.endpoint !== undefined ? { url: options.endpoint } : {}),
    ...(options.timeoutMillis !== undefined
      ? { timeoutMillis: options.timeoutMillis }
      : {}),
    ...(options.compression !== undefined
      ? {
          compression: options.compression as NonNullable<
            NonNullable<
              ConstructorParameters<typeof OTLPTraceExporter>[0]
            >['compression']
          >,
        }
      : {}),
  });
}
