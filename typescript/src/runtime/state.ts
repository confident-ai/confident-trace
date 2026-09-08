import { createContextKey } from '@opentelemetry/api';
import type { ContentPolicy } from '@/content/policy';
import type { TraceRuntime } from '@/runtime/types';
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
  auto: AutomaticState;
} = {
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
