import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import type { ResolvedExportOptions } from '@/config/types';
import { EXPORT_CONCURRENCY } from '@/exporters/bounded';

export function createHttpExporter(
  options: ResolvedExportOptions,
): OTLPTraceExporter {
  return new OTLPTraceExporter({
    concurrencyLimit: EXPORT_CONCURRENCY,
    headers: options.headers,
    ...(options.endpoint !== undefined ? { url: options.endpoint } : {}),
    ...(options.timeoutMillis !== undefined
      ? { timeoutMillis: options.timeoutMillis }
      : {}),
    ...(options.tlsSkipVerify
      ? { httpAgentOptions: { keepAlive: true, rejectUnauthorized: false } }
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
