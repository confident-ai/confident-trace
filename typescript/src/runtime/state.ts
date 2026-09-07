import { createContextKey } from '@opentelemetry/api';
import type { ContentPolicy } from '@/content/policy';
import type { TraceRuntime } from '@/runtime/types';

// A private shared build entry keeps all provider entry points on one runtime.
export const state: { runtime?: TraceRuntime; policy?: ContentPolicy } = {};

export const suppress = createContextKey('confident-trace.provider-call');
export const patched = new WeakSet<object>();
