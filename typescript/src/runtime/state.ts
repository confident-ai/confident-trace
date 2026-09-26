import { createContextKey } from '@opentelemetry/api';
import type { TracerProvider } from '@opentelemetry/api';
import type { SpanProcessor } from '@opentelemetry/sdk-trace-base';
import type { ContentPolicy } from '@/content/policy';
import type { TraceRuntime } from '@/runtime/types';
import type { ProjectRouter } from '@/runtime/scopes';
import type { InitOptions } from '@/config/types';
import type { InstrumentationName, InstrumentationState } from '@/auto/types';
import { instrumentationNames } from '@/auto/types';

// A private shared build entry keeps all provider entry points on one runtime.
interface AutomaticState {
  registered: boolean;
  selected: Set<InstrumentationName>;
  observed: Map<InstrumentationName, InstrumentationState>;
  warnings: Set<string>;
  pendingWarnings: Map<
    string,
    { message: string; integration?: InstrumentationName }
  >;
  activate: Set<() => void>;
  owned: Set<{
    flush?: () => Promise<void>;
    close: () => void | Promise<void>;
  }>;
  options: InitOptions;
}
export const state: {
  runtime?: TraceRuntime;
  policy?: ContentPolicy;
  /** init()'s provider; frameworks can add processors after construction. */
  ownedProvider?: {
    tracerProvider: TracerProvider;
    registerSpanProcessor(processor: SpanProcessor): void;
  };
  router?: ProjectRouter;
  /** Where LiveKit call recordings go: the OTLP/HTTP endpoint's sibling path. */
  recordingUpload?: { url: string; headers: Record<string, string> };
  /** Native framework scopes whose spans receive an integration label. */
  integrationScopes: Map<string, string>;
  auto: AutomaticState;
} = {
  integrationScopes: new Map(),
  auto: {
    registered: false,
    selected: new Set(instrumentationNames),
    observed: new Map(),
    warnings: new Set(),
    pendingWarnings: new Map(),
    activate: new Set(),
    owned: new Set(),
    options: {},
  },
};

export const suppress = createContextKey('confident-trace.provider-call');
export const patched = new WeakSet<object>();

export const automaticOriginals = new WeakMap<
  object,
  (...args: never[]) => unknown
>();

export const requestSuppressionKey = createContextKey(
  'confident-trace.request-suppressed',
);

export const traceContextKey = createContextKey(
  'confident-trace.trace-context',
);
export const deferTraceContextKey = createContextKey(
  'confident-trace.defer-trace-context',
);

// Plain output captured by a trusted framework integration, keyed by result identity.
export const frameworkOutputs = new WeakMap<object, unknown>();
