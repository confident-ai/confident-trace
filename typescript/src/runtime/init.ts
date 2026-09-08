import {
  configure,
  activate,
  getInstrumentationStatus,
  flushOwned,
  closeOwned,
} from '@/auto/control';
import {
  context,
  diag,
  propagation,
  trace,
  ProxyTracerProvider,
} from '@opentelemetry/api';
import { AsyncLocalStorageContextManager } from '@opentelemetry/context-async-hooks';
import {
  CompositePropagator,
  W3CBaggagePropagator,
  W3CTraceContextPropagator,
} from '@opentelemetry/core';
import {
  defaultResource,
  detectResources,
  envDetector,
  resourceFromAttributes,
} from '@opentelemetry/resources';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import type { SpanProcessor } from '@opentelemetry/sdk-trace-base';
import { isDisabled } from '@/config/resolve';
import type { InitOptions } from '@/config/types';
import { ContentPolicy } from '@/content/policy';
import { withinBudget } from '@/runtime/lifecycle';
import { createSpanProcessor } from '@/runtime/processor';
import type { TraceRuntime } from '@/runtime/types';
import { SCHEMA_URL } from '@/semconv/generated';
import { VERSION } from '@/runtime/version';
import { state } from '@/runtime/state';

const noop = new ProxyTracerProvider().getTracer('confident-trace-disabled');
const inactive: TraceRuntime = {
  active: false,
  getTracer: () => noop,
  getInstrumentationStatus,
  flush: async () => true,
  shutdown: async () => true,
};
let runtime: TraceRuntime | undefined;

class OwnedRuntime implements TraceRuntime {
  private running = true;
  private closing: Promise<void> | undefined;
  constructor(
    private readonly provider: NodeTracerProvider,
    readonly policy: ContentPolicy,
    private readonly contextManager?: AsyncLocalStorageContextManager,
  ) {}
  get active(): boolean {
    return this.running;
  }
  getInstrumentationStatus = getInstrumentationStatus;
  getTracer() {
    return this.running && !isDisabled()
      ? this.provider.getTracer('confident-trace', VERSION, {
          schemaUrl: SCHEMA_URL,
        })
      : noop;
  }
  flush(timeoutMillis = 30000): Promise<boolean> {
    return withinBudget(
      () => this.closing ?? flushOwned().then(() => this.provider.forceFlush()),
      timeoutMillis,
    );
  }
  shutdown(timeoutMillis = 5000): Promise<boolean> {
    if (!this.closing) {
      this.running = false;
      this.closing = Promise.resolve()
        .then(() => closeOwned())
        .finally(() => this.provider.shutdown())
        .finally(() => {
          this.contextManager?.disable();
        });
    }
    return withinBudget(() => this.closing!, timeoutMillis);
  }
}

export function init(options: InitOptions = {}): TraceRuntime {
  if (isDisabled()) {
    if (runtime?.active) void runtime.shutdown();
    return inactive;
  }
  // A registered provider cannot be replaced after shutdown. Reinitialization
  // returns the terminal runtime instead of pretending a new pipeline is global.
  if (runtime) return runtime;
  configure(options);
  let processor: SpanProcessor | undefined;
  let provider: NodeTracerProvider | undefined;
  let ownedContext: AsyncLocalStorageContextManager | undefined;
  try {
    const policy = new ContentPolicy(options);
    processor = createSpanProcessor(options);
    const resource = defaultResource()
      .merge(detectResources({ detectors: [envDetector] }))
      .merge(resourceFromAttributes(options.resourceAttributes ?? {}));
    provider = new NodeTracerProvider({
      resource,
      spanProcessors: [processor],
    });
    if (!trace.setGlobalTracerProvider(provider)) {
      void provider.shutdown().catch(() => {});
      diag.warn(
        'Confident Trace could not register: install createSpanProcessor() when constructing your existing provider',
      );
      return inactive;
    }
    const manager = new AsyncLocalStorageContextManager().enable();
    if (context.setGlobalContextManager(manager)) ownedContext = manager;
    else manager.disable();
    propagation.setGlobalPropagator(
      new CompositePropagator({
        propagators: [
          new W3CTraceContextPropagator(),
          new W3CBaggagePropagator(),
        ],
      }),
    );
    runtime = new OwnedRuntime(provider, policy, ownedContext);
    state.runtime = runtime;
    state.policy = policy;
    activate();
    return runtime;
  } catch {
    const cleanup = provider ?? processor;
    if (cleanup)
      void Promise.resolve()
        .then(() => cleanup.shutdown())
        .catch(() => {});
    ownedContext?.disable();
    diag.warn('Confident Trace initialization failed; tracing is disabled');
    return inactive;
  }
}

export function flush(timeoutMillis = 30000): Promise<boolean> {
  return runtime?.flush(timeoutMillis) ?? Promise.resolve(true);
}
export function shutdown(timeoutMillis = 5000): Promise<boolean> {
  return runtime?.shutdown(timeoutMillis) ?? Promise.resolve(true);
}
