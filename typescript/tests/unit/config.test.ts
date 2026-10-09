import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import { isDisabled, resolveExportOptions } from '@/config/resolve';
import { createHttpExporter } from '@/exporters/http';

vi.mock('@opentelemetry/exporter-trace-otlp-proto', () => ({
  OTLPTraceExporter: vi.fn(),
}));

beforeEach(() => {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith('OTEL_') || key.startsWith('CONFIDENT_'))
      vi.stubEnv(key, undefined);
  }
});
afterEach(() => vi.unstubAllEnvs());

describe('export configuration', () => {
  it('defaults to Confident HTTP/protobuf', () => {
    expect(resolveExportOptions({})).toEqual({
      protocol: 'http/protobuf',
      endpoint: 'https://otel.confident-ai.com/v1/traces',
      headers: {},
      compression: 'gzip',
      tlsSkipVerify: false,
    });
  });
  it('uses explicit then trace-specific then generic options', () => {
    vi.stubEnv('OTEL_EXPORTER_OTLP_PROTOCOL', 'grpc');
    vi.stubEnv('OTEL_EXPORTER_OTLP_TRACES_PROTOCOL', 'http/protobuf');
    vi.stubEnv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318');
    expect(resolveExportOptions({})).toEqual({
      protocol: 'http/protobuf',
      endpoint: 'https://otel.confident-ai.com/v1/traces',
      headers: {},
      compression: 'gzip',
      tlsSkipVerify: false,
    });
    expect(
      resolveExportOptions({
        protocol: 'grpc',
        endpoint: 'http://localhost:4317',
        timeoutMillis: 50,
        compression: 'gzip',
      }),
    ).toMatchObject({
      protocol: 'grpc',
      endpoint: 'http://localhost:4317',
      timeoutMillis: 50,
      compression: 'gzip',
      tlsSkipVerify: false,
    });
  });
  it('compresses payloads unless opted out', () => {
    expect(resolveExportOptions({})).toMatchObject({ compression: 'gzip' });
    expect(resolveExportOptions({ compression: 'none' })).toMatchObject({
      compression: 'none',
    });
    vi.stubEnv('OTEL_EXPORTER_OTLP_COMPRESSION', 'none');
    expect(resolveExportOptions({})).toMatchObject({ compression: 'none' });
    expect(resolveExportOptions({ compression: 'gzip' })).toMatchObject({
      compression: 'gzip',
      tlsSkipVerify: false,
    });
    vi.stubEnv('OTEL_EXPORTER_OTLP_TRACES_COMPRESSION', 'gzip');
    expect(resolveExportOptions({})).toMatchObject({ compression: 'gzip' });
    // A misspelled environment value keeps the default rather than throwing.
    vi.stubEnv('OTEL_EXPORTER_OTLP_TRACES_COMPRESSION', 'gzipp');
    expect(resolveExportOptions({})).toMatchObject({ compression: 'gzip' });
  });
  it('uses the Confident endpoint before OTel variables and after explicit options', () => {
    vi.stubEnv(
      'CONFIDENT_OTEL_ENDPOINT',
      'https://eu.otel.confident-ai.com/v1/traces',
    );
    vi.stubEnv('OTEL_EXPORTER_OTLP_ENDPOINT', 'https://generic.invalid');
    vi.stubEnv(
      'OTEL_EXPORTER_OTLP_TRACES_ENDPOINT',
      'https://signal.invalid/traces',
    );
    expect(resolveExportOptions({}).endpoint).toBe(
      'https://eu.otel.confident-ai.com/v1/traces',
    );
    expect(
      resolveExportOptions({ endpoint: 'https://explicit.invalid/traces' })
        .endpoint,
    ).toBe('https://explicit.invalid/traces');
    vi.stubEnv('CONFIDENT_OTEL_ENDPOINT', '');
    expect(resolveExportOptions({}).endpoint).toBe(
      'https://otel.confident-ai.com/v1/traces',
    );
    expect(() => resolveExportOptions({ protocol: 'grpc' })).toThrow();
  });
  it('merges decoded headers, API key, and explicit headers case-insensitively', () => {
    vi.stubEnv('OTEL_EXPORTER_OTLP_HEADERS', 'generic=ignored');
    vi.stubEnv(
      'OTEL_EXPORTER_OTLP_TRACES_HEADERS',
      'custom=hello%20world,x-confident-api-key=env,bad=%ZZ',
    );
    vi.stubEnv('CONFIDENT_API_KEY', 'secret');
    expect(
      resolveExportOptions({ headers: { 'X-CONFIDENT-API-KEY': 'explicit' } })
        .headers,
    ).toEqual({ custom: 'hello world', 'x-confident-api-key': 'explicit' });
    expect(
      resolveExportOptions({ apiKey: '' }).headers['x-confident-api-key'],
    ).toBe('env');
    vi.stubEnv('OTEL_EXPORTER_OTLP_TRACES_HEADERS', '');
    expect(resolveExportOptions({ apiKey: '' }).headers).toEqual({});
  });
  it('requires a gRPC endpoint and validates explicit budgets', () => {
    expect(() => resolveExportOptions({ protocol: 'grpc' })).toThrow();
    for (const timeoutMillis of [-1, 0, NaN, Infinity]) {
      expect(() => resolveExportOptions({ timeoutMillis })).toThrow();
    }
  });
  it('honors disabled irrespective of explicit options', () => {
    vi.stubEnv('OTEL_SDK_DISABLED', 'TRUE');
    expect(isDisabled()).toBe(true);
  });
  it('skips TLS verification only when CONFIDENT_OTEL_TLS_SKIP_VERIFY is "true"', () => {
    const agentOptions = (value: string | undefined) => {
      vi.stubEnv('CONFIDENT_OTEL_TLS_SKIP_VERIFY', value);
      createHttpExporter(resolveExportOptions({}));
      return vi.mocked(OTLPTraceExporter).mock.lastCall?.[0]?.httpAgentOptions;
    };
    expect(agentOptions(undefined)).toBeUndefined();
    expect(agentOptions('TRUE')).toBeUndefined();
    expect(agentOptions('1')).toBeUndefined();
    expect(agentOptions('true')).toEqual({
      keepAlive: true,
      rejectUnauthorized: false,
    });
  });
  it.each([undefined, 'true', 'false', 'TRUE', '1'])(
    'explicit TLS options override environment %s and remain stable',
    (value) => {
      for (const explicit of [true, false]) {
        vi.stubEnv('CONFIDENT_OTEL_TLS_SKIP_VERIFY', value);
        const resolved = resolveExportOptions({ tlsSkipVerify: explicit });
        vi.stubEnv('CONFIDENT_OTEL_TLS_SKIP_VERIFY', String(!explicit));
        createHttpExporter(resolved);
        expect(
          vi.mocked(OTLPTraceExporter).mock.lastCall?.[0]?.httpAgentOptions,
        ).toEqual(
          explicit ? { keepAlive: true, rejectUnauthorized: false } : undefined,
        );
      }
    },
  );
  it('rejects non-boolean TLS options', () => {
    expect(() =>
      resolveExportOptions({ tlsSkipVerify: 'false' as unknown as boolean }),
    ).toThrow('tlsSkipVerify must be a boolean');
  });
});
