import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isDisabled, resolveExportOptions } from '@/config/resolve';

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
    });
  });
  it('uses explicit then trace-specific then generic options', () => {
    vi.stubEnv('OTEL_EXPORTER_OTLP_PROTOCOL', 'grpc');
    vi.stubEnv('OTEL_EXPORTER_OTLP_TRACES_PROTOCOL', 'http/protobuf');
    vi.stubEnv('OTEL_EXPORTER_OTLP_ENDPOINT', 'http://localhost:4318');
    expect(resolveExportOptions({})).toEqual({
      protocol: 'http/protobuf',
      headers: {},
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
    });
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
    expect(resolveExportOptions({}).endpoint).toBeUndefined();
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
});
