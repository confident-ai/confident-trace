import type { InstrumentationStatus } from '@/auto/types';
import type { Tracer } from '@opentelemetry/api';

export interface TraceRuntime {
  readonly active: boolean;
  getTracer(): Tracer;
  getInstrumentationStatus(): InstrumentationStatus;
  flush(timeoutMillis?: number): Promise<boolean>;
  shutdown(timeoutMillis?: number): Promise<boolean>;
}
