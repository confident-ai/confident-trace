export { init, flush, shutdown } from '@/runtime/init';
export type { InitOptions } from '@/config/types';
export type { TraceRuntime } from '@/runtime/types';
export {
  span,
  withSpan,
  turn,
  updateSpan,
  updateTrace,
  traceContext,
  updateLlmSpan,
} from '@/spans/index';
export type {
  SpanType,
  LlmFields,
  ThreadFields,
  SpanFields,
  TraceFields,
  SpanOptions,
  TurnOptions,
} from '@/spans/index';

export type {
  InstrumentationName,
  InstrumentationState,
  InstrumentationStatus,
} from '@/auto/types';

export { withProject, withTracingSuppressed } from '@/runtime/scopes';
