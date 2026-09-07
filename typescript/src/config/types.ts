import type { Attributes } from '@opentelemetry/api';
import type { SpanExporter } from '@opentelemetry/sdk-trace-base';
import type { ContentOptions } from '@/content/types';

export interface ExportOptions {
  apiKey?: string;
  endpoint?: string;
  protocol?: 'http/protobuf' | 'grpc';
  headers?: Readonly<Record<string, string>>;
  /** All explicit TypeScript timeouts are milliseconds. */
  timeoutMillis?: number;
  compression?: 'gzip' | 'none';
  /** Ownership transfers to the returned processor/runtime. */
  exporter?: SpanExporter;
}

export interface InitOptions extends ExportOptions, ContentOptions {
  resourceAttributes?: Attributes;
}

export interface ResolvedExportOptions {
  protocol: 'http/protobuf' | 'grpc';
  endpoint?: string;
  headers: Record<string, string>;
  timeoutMillis?: number;
  compression?: 'gzip' | 'none';
}
