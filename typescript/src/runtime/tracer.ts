import { trace } from '@opentelemetry/api';
import type { Tracer } from '@opentelemetry/api';
import { state } from '@/runtime/state';
import { VERSION } from '@/runtime/version';
import { SCHEMA_URL } from '@/semconv/generated';

/**
 * The tracer for this package's spans: on init()'s provider, which is not the
 * global provider, or on the global provider when init() wasn't called.
 */
export function confidentTracer(): Tracer {
  const provider =
    state.ownedProvider?.tracerProvider ?? trace.getTracerProvider();
  return provider.getTracer('confident-trace', VERSION, {
    schemaUrl: SCHEMA_URL,
  });
}
