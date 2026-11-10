import type { InstrumentationName } from '@/auto/types';
import type { Attributes } from '@opentelemetry/api';
import type {
  SpanExporter,
  SpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import type { ContentOptions } from '@/content/types';

export interface ExportOptions {
  apiKey?: string;
  endpoint?: string;
  protocol?: 'http/protobuf' | 'grpc';
  headers?: Readonly<Record<string, string>>;
  /** All explicit TypeScript timeouts are milliseconds. */
  timeoutMillis?: number;
  compression?: 'gzip' | 'none';
  /** Skip certificate verification for Confident HTTP exports and recording uploads. */
  tlsSkipVerify?: boolean;
  /** Ownership transfers to the returned processor/runtime. */
  exporter?: SpanExporter;
  /** Synchronous factory; each returned exporter is owned by this runtime. */
  projectExporterFactory?: (apiKey: string) => SpanExporter;
  /** Export every span; by default only Confident/GenAI spans and their ancestors export. */
  exportNonAiSpans?: boolean;
}

export interface InitOptions extends ExportOptions, ContentOptions {
  litellmProxyUrls?: readonly string[];
  openrouterProxyUrls?: readonly string[];
  portkeyProxyUrls?: readonly string[];
  bifrostProxyUrls?: readonly string[];
  truefoundryProxyUrls?: readonly string[];

  /** Automatic integrations enabled by the preload. [] selects manual-only tracing. */
  instrumentations?: 'all' | readonly InstrumentationName[];
  resourceAttributes?: Attributes;
  /**
   * More processors for the package-owned provider, run after its exporting
   * processor and shut down with the runtime.
   */
  spanProcessors?: readonly SpanProcessor[];
  /**
   * Register the package-owned provider and propagator globally (default), so
   * other OTel spans are recorded too. `false` keeps them private, so the
   * application can register its own later, and works beside one it already has.
   */
  globalProvider?: boolean;
}

export interface ResolvedExportOptions {
  protocol: 'http/protobuf' | 'grpc';
  endpoint?: string;
  headers: Record<string, string>;
  timeoutMillis?: number;
  compression?: 'gzip' | 'none';
  /** Skip certificate verification for Confident HTTP exports and recording uploads. */
  tlsSkipVerify?: boolean;
}
