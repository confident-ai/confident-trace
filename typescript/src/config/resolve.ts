import type { ExportOptions, ResolvedExportOptions } from '@/config/types';

export function isDisabled(): boolean {
  return process.env.OTEL_SDK_DISABLED?.toLowerCase() === 'true';
}

export function resolveExportOptions(
  options: ExportOptions,
): ResolvedExportOptions {
  const env = process.env;
  const protocol =
    options.protocol ??
    env.OTEL_EXPORTER_OTLP_TRACES_PROTOCOL ??
    env.OTEL_EXPORTER_OTLP_PROTOCOL ??
    'http/protobuf';
  if (protocol !== 'http/protobuf' && protocol !== 'grpc') {
    throw new Error('Unsupported OTLP protocol');
  }
  const result: ResolvedExportOptions = { protocol, headers: {} };
  if (options.endpoint !== undefined) result.endpoint = options.endpoint;
  else if (
    !env.OTEL_EXPORTER_OTLP_TRACES_ENDPOINT &&
    !env.OTEL_EXPORTER_OTLP_ENDPOINT
  ) {
    if (protocol === 'grpc')
      throw new Error('A gRPC collector endpoint is required');
    result.endpoint = 'https://otel.confident-ai.com/v1/traces';
  }
  // Keep omitted endpoint/transport values omitted so OTel applies its own
  // signal-specific environment precedence, TLS and path resolution.
  const rawHeaders =
    env.OTEL_EXPORTER_OTLP_TRACES_HEADERS ??
    env.OTEL_EXPORTER_OTLP_HEADERS ??
    '';
  for (const item of rawHeaders.split(',')) {
    const separator = item.indexOf('=');
    if (separator < 1) continue;
    try {
      result.headers[item.slice(0, separator).trim().toLowerCase()] =
        decodeURIComponent(item.slice(separator + 1).trim());
    } catch {
      /* Ignore malformed environment header values. */
    }
  }
  const apiKey = options.apiKey ?? env.CONFIDENT_API_KEY;
  if (apiKey) result.headers['x-confident-api-key'] = apiKey;
  for (const [key, value] of Object.entries(options.headers ?? {})) {
    result.headers[key.toLowerCase()] = value;
  }
  if (options.timeoutMillis !== undefined) {
    if (!Number.isFinite(options.timeoutMillis) || options.timeoutMillis <= 0) {
      throw new Error('timeoutMillis must be positive and finite');
    }
    result.timeoutMillis = options.timeoutMillis;
  }
  if (options.compression !== undefined) {
    if (options.compression !== 'gzip' && options.compression !== 'none') {
      throw new Error('Unsupported compression');
    }
    result.compression = options.compression;
  }
  return result;
}
