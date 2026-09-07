import { Metadata } from '@grpc/grpc-js';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-grpc';
import type { ResolvedExportOptions } from '@/config/types';

export function createGrpcExporter(
  options: ResolvedExportOptions,
): OTLPTraceExporter {
  const metadata = new Metadata();
  for (const [key, value] of Object.entries(options.headers))
    metadata.set(key, value);
  return new OTLPTraceExporter({
    metadata,
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
