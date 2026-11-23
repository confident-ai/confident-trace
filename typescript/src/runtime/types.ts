import type { InstrumentationStatus } from '@/auto/types';
import type { Tracer, TracerProvider } from '@opentelemetry/api';

export interface TraceRuntime {
  readonly active: boolean;
  /** The provider this runtime records on, which isn't global with `globalProvider: false`. */
  readonly provider: TracerProvider;
  getTracer(): Tracer;
  getInstrumentationStatus(): InstrumentationStatus;
  flush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<boolean>;
}
