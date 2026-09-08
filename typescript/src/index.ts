export { init, flush, shutdown } from '@/runtime/init';
export { createSpanProcessor } from '@/runtime/processor';
export type { InitOptions, ExportOptions } from '@/config/types';
export type { TraceRuntime } from '@/runtime/types';
export { span, withSpan, turn, updateSpan, updateTrace } from '@/spans/index';
export type {
  SpanType,
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
