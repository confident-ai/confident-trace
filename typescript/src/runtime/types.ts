import type { Tracer } from '@opentelemetry/api';

export interface TraceRuntime {
  readonly active: boolean;
  getTracer(): Tracer;
  flush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<boolean>;
}
