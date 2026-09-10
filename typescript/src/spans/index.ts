import { types } from 'node:util';
import {
  context,
  trace,
  SpanKind,
  SpanStatusCode,
  createContextKey,
  isSpanContextValid,
} from '@opentelemetry/api';
import type {
  Attributes,
  Context,
  Span,
  SpanContext,
  Tracer,
} from '@opentelemetry/api';
import { ContentPolicy } from '@/content/policy';
import type { ContentOptions } from '@/content/types';
import { isDisabled } from '@/config/resolve';
import { state, traceContextKey, deferTraceContextKey } from '@/runtime/state';
import { detachedContext } from '@/runtime/scopes';
import {
  applyLlmFields,
  applyThreadFields,
  validateFields,
  llmAttributes,
} from '@/spans/fields';
import type { ThreadFields, LlmFields } from '@/spans/fields';
export type { ThreadFields, LlmFields } from '@/spans/fields';
import { VERSION } from '@/runtime/version';
import * as S from '@/semconv/generated';

export type SpanType = 'agent' | 'llm' | 'retriever' | 'tool' | 'custom';
export interface SpanFields {
  metricCollection?: string;
  name?: string;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown> | null;
  retrievalContext?: readonly string[] | null;
  context?: readonly string[] | null;
  expectedOutput?: unknown;
  toolsCalled?: readonly Record<string, unknown>[] | null;
  expectedTools?: readonly Record<string, unknown>[] | null;
}
export interface TraceFields extends SpanFields {
  thread?: ThreadFields;
  testCaseId?: string;
  tags?: readonly string[];
  userId?: string;
  threadId?: string;
  turnId?: string;
  environment?: string;
}
export type SpanOptions = SpanFields &
  ContentOptions & {
    attributes?: Attributes;
    tracer?: Tracer;
  } & (
    | ({ type: 'llm' } & LlmFields)
    | ({ type?: Exclude<SpanType, 'llm'> } & { [K in keyof LlmFields]?: never })
  );
export type TurnOptions = TraceFields &
  ContentOptions & {
    previous?: SpanContext;
    tracer?: Tracer;
  } & ({ threadId: string } | { thread: ThreadFields & { id: string } });
const roles: readonly string[] = [
  'agent',
  'llm',
  'retriever',
  'tool',
  'custom',
];
const contentKeys: Record<string, string> = {
  input: 'input',
  output: 'output',
  metadata: 'metadata',
  retrievalContext: 'retrieval_context',
  context: 'context',
  expectedOutput: 'expected_output',
  toolsCalled: 'tools_called',
  expectedTools: 'expected_tools',
};
const traceKeys: Record<string, string> = {
  name: 'name',
  tags: 'tags',
  userId: 'user_id',
  threadId: 'thread_id',
  turnId: 'turn_id',
  environment: 'environment',
  testCaseId: 'test_case_id',
};
const scalarKeys: Record<string, string> = { metricCollection: 'metric_collection' };
const spanFields = new Set(['name', ...Object.keys(contentKeys), ...Object.keys(scalarKeys)]);
const traceFields = new Set([
  ...spanFields,
  ...Object.keys(traceKeys),
  'thread',
]);
const configuration = ['captureContent', 'maxContentBytes', 'redact', 'tracer'];
const spanOptions = new Set([
  ...spanFields,
  ...configuration,
  ...Object.keys(llmAttributes),
  'type',
  'attributes',
]);
const turnOptions = new Set([...traceFields, ...configuration, 'previous']);
const entryKey = createContextKey('confident-trace.entry-operation');
const operationKey = createContextKey('confident-trace.operation');
// Weak keys track explicit writes without retaining ended spans.
const writes = new WeakMap<Span, Set<string>>();
function safe(action: () => void): void {
  try {
    action();
  } catch {
    /* Telemetry is best effort. */
  }
}
function validate(options: object, allowed: Set<string>): void {
  validateFields(options);
  for (const key of Object.keys(options)) {
    if (!allowed.has(key))
      throw new TypeError(`Unknown tracing option: ${key}`);
  }
  if (
    'name' in options &&
    options.name !== undefined &&
    typeof options.name !== 'string'
  )
    throw new TypeError('Span name must be a string');
}
function role(options: SpanOptions): SpanType {
  const value =
    options.type !== undefined
      ? options.type
      : (options.attributes?.[S.ATTR_CONFIDENT_SPAN_TYPE] ?? 'custom');
  if (typeof value !== 'string' || !roles.includes(value))
    throw new TypeError(`Unsupported span type: ${String(value)}`);
  if (
    Object.keys(llmAttributes).some(
      (k) => (options as Record<string, unknown>)[k] !== undefined,
    ) &&
    value !== 'llm'
  )
    throw new TypeError('Model and usage options require type=llm');
  return value as SpanType;
}
function policy(
  options: ContentOptions = {},
  parent = context.active(),
): ContentPolicy {
  const inherited =
    (parent.getValue(operationKey) as Operation | undefined)?.policy ??
    state.policy;
  return new ContentPolicy({
    captureContent: inherited?.enabled ?? true,
    maxContentBytes: options.maxContentBytes ?? inherited?.maxBytes ?? 16384,
    ...(options.redact ? { redact: options.redact } : {}),
  });
}
// withOptions preserves the inherited redactor as well as the global opt-out.
function effectivePolicy(
  options: ContentOptions,
  parent: Context,
): ContentPolicy {
  const inherited =
    (parent.getValue(operationKey) as Operation | undefined)?.policy ??
    state.policy;
  if (!inherited) return policy(options, parent);
  return inherited.withOptions({
    ...(options.maxContentBytes !== undefined
      ? { maxContentBytes: options.maxContentBytes }
      : {}),
    ...(options.redact !== undefined ? { redact: options.redact } : {}),
    captureContent: inherited.enabled && (state.policy?.enabled ?? true),
  });
}
function remember(span: Span, key: string): void {
  let set = writes.get(span);
  if (!set) {
    set = new Set();
    writes.set(span, set);
  }
  set.add(key);
}
function putContent(
  span: Span,
  key: string,
  value: unknown,
  contentPolicy: ContentPolicy,
): void {
  if (!span.isRecording()) return;
  const encoded = contentPolicy.encode(value);
  if (encoded !== undefined) span.setAttribute(key, encoded);
}
function apply(
  span: Span,
  fields: SpanFields | TraceFields,
  scope: 'span' | 'trace',
  contentPolicy: ContentPolicy,
  onlyUnset = false,
): void {
  if (!span.isRecording()) return;
  const attrs = (span as Span & { attributes?: Attributes }).attributes ?? {};
  if (onlyUnset) {
    fields = Object.fromEntries(Object.entries(fields).filter(([key]) => {
      if (key === 'thread') return !['confident.trace.thread.id', 'confident.trace.thread_id', 'confident.trace.thread.tags', 'confident.trace.thread.metadata'].some(attr => Object.hasOwn(attrs, attr));
      const attr = `confident.${scope}.${contentKeys[key] ?? scalarKeys[key] ?? traceKeys[key]}`;
      return !Object.hasOwn(attrs, attr) && !writes.get(span)?.has(attr);
    }));
  }
  if (scope === 'trace')
    applyThreadFields(span, fields as TraceFields, contentPolicy);
  for (const [key, value] of Object.entries(fields)) {
    if (value === undefined) continue;
    if (key === 'name' && scope === 'span') {
      safe(() => span.updateName(String(value).slice(0, 4096)));
      continue;
    }
    const suffix =
      contentKeys[key] ?? scalarKeys[key] ?? (scope === 'trace' ? traceKeys[key] : undefined);
    if (!suffix) continue;
    const attribute = `confident.${scope}.${suffix}`;
    remember(span, attribute);
    safe(() => {
      if (contentKeys[key]) putContent(span, attribute, value, contentPolicy);
      else if (
        key === 'tags' &&
        Array.isArray(value) &&
        value.every((v) => typeof v === 'string')
      )
        span.setAttribute(attribute, value.slice(0, 128));
      else if (typeof value === 'string')
        span.setAttribute(attribute, value.slice(0, 4096));
    });
  }
}
function update(
  fields: SpanFields | TraceFields,
  scope: 'span' | 'trace',
  onlyUnset = false,
): void {
  if (isDisabled()) return;
  const active = context.active();
  const entry = active.getValue(entryKey) as Operation | undefined;
  const current = trace.getSpan(active);
  const target = scope === 'trace' ? (entry?.span ?? current) : current;
  if (!target) return;
  const owner =
    scope === 'trace' && entry
      ? entry
      : (active.getValue(operationKey) as Operation | undefined);
  safe(() =>
    apply(
      target,
      fields,
      scope,
      owner?.policy ?? state.policy ?? new ContentPolicy(),
      onlyUnset,
    ),
  );
}
/** Update the active OTel span. Explicit fields override automatic capture. */
export function updateSpan(fields: SpanFields & LlmFields): void {
  validate(fields, new Set([...spanFields, ...Object.keys(llmAttributes)]));
  update(fields, 'span');
  if (isDisabled()) return;
  const current = trace.getSpan(context.active());
  if (current) safe(() => updateLlmFields(current, fields));
}
/** Update the package entry span, or the current OTel span if there is no entry. */
export function updateTrace(fields: TraceFields): void {
  validate(fields, traceFields);
  update(fields, 'trace');
}

/** Establish ambient defaults without a span; updateTrace explicitly replaces fields. */
export function traceContext<T>(fields: TraceFields, callback: () => T): T {
  validate(fields, traceFields);
  const parent = context.active();
  const defaults = {
    ...Object.fromEntries(Object.entries(fields).filter(([, value]) => value !== undefined)),
    ...(parent.getValue(traceContextKey) as TraceFields | undefined),
  };
  update(defaults, 'trace', true);
  return context.with(parent.setValue(traceContextKey, defaults), callback);
}

export function ambientOnStart(span: Span, parent: Context): void {
  if (isDisabled() || parent.getValue(deferTraceContextKey) || trace.getSpan(parent)?.isRecording()) return;
  safe(() => apply(span, (parent.getValue(traceContextKey) as TraceFields | undefined) ?? {}, 'trace', state.policy ?? new ContentPolicy(), true));
}

const warnedLlmTargets = new Set<string>();
function updateLlmFields(span: Span, fields: LlmFields): void {
  if (
    !span.isRecording() ||
    !Object.keys(llmAttributes).some(
      (key) => (fields as Record<string, unknown>)[key] !== undefined,
    )
  )
    return;
  const attrs = (span as Span & { attributes?: Attributes }).attributes ?? {};
  const category = attrs[S.ATTR_CONFIDENT_SPAN_TYPE];
  const modelSpan =
    category === 'llm' ||
    (category === undefined &&
      ['chat', 'generate_content', 'text_completion', 'embeddings'].includes(
        String(attrs[S.ATTR_GEN_AI_OPERATION_NAME]),
      ));
  if (modelSpan) applyLlmFields(span, fields);
  else if (
    !warnedLlmTargets.has(
      roles.includes(String(category)) ? String(category) : 'unclassified',
    )
  ) {
    warnedLlmTargets.add(
      roles.includes(String(category)) ? String(category) : 'unclassified',
    );
    console.warn(
      '[confident-trace] updateSpan skipped LLM fields: the active span is not an LLM span. Use span({ type: "llm" }, ...) or update inside an instrumented model span. General span fields were still applied.',
    );
  }
}
/** Compatibility alias for LLM fields; prefer updateSpan. */
export function updateLlmSpan(fields: LlmFields): void {
  validate(fields, new Set(Object.keys(llmAttributes)));
  updateSpan(fields);
}

class Operation {
  readonly span: Span;
  readonly active: Context;
  readonly policy: ContentPolicy;
  readonly entry: boolean;
  private ended = false;
  constructor(
    name: string,
    options: SpanOptions,
    parent: Context,
    traceValues?: TraceFields,
    previous?: SpanContext,
  ) {
    this.policy = effectivePolicy(options, parent);
    const attributes = {
      ...options.attributes,
      [S.ATTR_CONFIDENT_SPAN_TYPE]: role(options),
    };
    if (role(options) === 'tool')
      Object.assign(attributes, {
        [S.ATTR_GEN_AI_OPERATION_NAME]: 'execute_tool',
        [S.ATTR_GEN_AI_TOOL_NAME]: name,
      });
    const tracer =
      options.tracer ??
      trace
        .getTracerProvider()
        .getTracer('confident-trace', VERSION, { schemaUrl: S.SCHEMA_URL });
    this.span = tracer.startSpan(
      name,
      {
        kind: SpanKind.INTERNAL,
        attributes,
        ...(previous && isSpanContextValid(previous)
          ? { links: [{ context: previous }] }
          : {}),
      },
      parent.setValue(deferTraceContextKey, true),
    );
    this.entry = !parent.getValue(entryKey);
    this.active = trace.setSpan(parent, this.span).setValue(operationKey, this);
    if (this.entry) this.active = this.active.setValue(entryKey, this);
    for (const key of Object.keys(options.attributes ?? {}))
      remember(this.span, key);
    safe(() => apply(this.span, options, 'span', this.policy));
    safe(() => applyLlmFields(this.span, options));
    const parentSpan = trace.getSpan(parent) as
      (Span & { attributes?: Attributes }) | undefined;
    const conversation = parentSpan?.attributes?.['gen_ai.conversation.id'];
    if (typeof conversation === 'string')
      this.span.setAttribute('gen_ai.conversation.id', conversation);
    if (this.entry)
      safe(() =>
        apply(
          this.span,
          traceValues ?? {},
          'trace',
          this.policy,
        ),
      );
    if (this.entry) {
      safe(() => apply(this.span, (parent.getValue(traceContextKey) as TraceFields | undefined) ?? {}, 'trace', this.policy, true));
      safe(() => apply(this.span, { name }, 'trace', this.policy, true));
    }
  }
  run<T>(fn: () => T): T {
    return context.with(this.active, fn);
  }
  auto(field: 'input' | 'output', value: unknown): void {
    if (value === undefined) return;
    for (const scope of this.entry ? ['span', 'trace'] : ['span']) {
      const key = `confident.${scope}.${field}`;
      safe(() => {
        const attributes = (this.span as Span & { attributes?: Attributes })
          .attributes;
        if (!writes.get(this.span)?.has(key) && attributes?.[key] === undefined)
          putContent(this.span, key, value, this.policy);
      });
    }
  }
  end(failed = false): void {
    if (this.ended) return;
    this.ended = true;
    if (failed)
      safe(() => {
        this.span.setStatus({ code: SpanStatusCode.ERROR });
        this.span.setAttribute(S.ATTR_ERROR_TYPE, 'application_error');
      });
    safe(() => this.span.end());
  }
}
function enabled(options: { tracer?: Tracer }): boolean {
  return (
    !isDisabled() &&
    !(state.runtime && !state.runtime.active && !options.tracer)
  );
}
function start(
  name: string,
  options: SpanOptions,
  parent: Context,
  values?: TraceFields,
  previous?: SpanContext,
): Operation | undefined {
  if (!enabled(options)) return;
  try {
    return new Operation(name, options, parent, values, previous);
  } catch {
    return;
  }
}
function execute<T>(
  op: Operation | undefined,
  fn: () => T,
  capture: boolean,
): T {
  if (!op) return fn();
  const done = (value: unknown) => {
    if (capture) op.auto('output', value);
    op.end();
    return value;
  };
  const failed = (error: unknown): never => {
    op.end(true);
    throw error;
  };
  try {
    const value = op.run(fn);
    let then: unknown;
    if (
      value !== null &&
      (typeof value === 'object' || typeof value === 'function')
    )
      safe(() => {
        then = Reflect.get(value, 'then');
      });
    if (typeof then === 'function')
      return Promise.resolve(value).then(done, failed) as T;
    done(value);
    return value;
  } catch (error) {
    return failed(error);
  }
}
/** Execute a scoped callback now; sync stays sync and promises settle before ending. */
export function withSpan<T>(
  options: SpanOptions,
  callback: (span: Span) => T,
): T {
  validate(options, spanOptions);
  role(options);
  effectivePolicy(options, context.active());
  const op = start(options.name ?? 'span', options, context.active());
  return execute(
    op,
    () =>
      callback(
        op?.span ??
          trace.wrapSpanContext({
            traceId: '0'.repeat(32),
            spanId: '0'.repeat(16),
            traceFlags: 0,
          }),
      ),
    options.captureContent !== false,
  );
}
/** Execute a conversation turn in a new trace, optionally linking a previous span. */
export function turn<T>(options: TurnOptions, callback: (span: Span) => T): T {
  validate(options, turnOptions);
  if (typeof (options.threadId ?? options.thread?.id) !== 'string')
    throw new TypeError('turn requires threadId or thread.id');
  const parent = detachedContext();
  effectivePolicy(options, parent);
  const config: SpanOptions = Object.fromEntries(
    Object.entries(options).filter(([key]) => configuration.includes(key)),
  );
  const op = start(
    options.name ?? 'agent turn',
    config,
    parent,
    options,
    options.previous,
  );
  return execute(
    op,
    () =>
      callback(
        op?.span ??
          trace.wrapSpanContext({
            traceId: '0'.repeat(32),
            spanId: '0'.repeat(16),
            traceFlags: 0,
          }),
      ),
    options.captureContent !== false,
  );
}

type IteratorMethod = 'next' | 'return' | 'throw';
type NativeIterator =
  | Iterator<unknown, unknown, unknown>
  | AsyncIterator<unknown, unknown, unknown>;
function generator(
  iterator: NativeIterator,
  factory: () => Operation | undefined,
  capture: boolean,
  asynchronous: boolean,
): unknown {
  let op: Operation | undefined;
  let begun = false;
  let ended = false;
  const resume = (method: IteratorMethod, args: unknown[]) => {
    if (!ended && !begun && method === 'next') {
      begun = true;
      op = factory();
    }
    const invoke = () => Reflect.apply(iterator[method]!, iterator, args);
    return op && !ended ? op.run(invoke) : invoke();
  };
  const finish = (result: IteratorResult<unknown>) => {
    if (result.done && !ended) {
      ended = true;
      if (capture) op?.auto('output', result.value);
      op?.end();
    }
    return result;
  };
  const failed = (error: unknown): never => {
    ended = true;
    op?.end(true);
    throw error;
  };
  let busy = false;
  const sync = (method: IteratorMethod, args: unknown[]) => {
    if (busy) throw new TypeError('Generator is already running');
    busy = true;
    try {
      return finish(resume(method, args));
    } catch (error) {
      return failed(error);
    } finally {
      busy = false;
    }
  };
  // Native async generators queue requests. Queue context activation with each resume too.
  let tail = Promise.resolve<unknown>(undefined);
  const asyncStep = (method: IteratorMethod, args: unknown[]) => {
    const result = tail.then(async () => {
      try {
        return finish(await resume(method, args));
      } catch (error) {
        return failed(error);
      }
    });
    tail = result.catch(() => undefined);
    return result;
  };
  const step = asynchronous ? asyncStep : sync;
  return {
    next: (...args: unknown[]) => step('next', args),
    return: (...args: unknown[]) => step('return', args),
    throw: (...args: unknown[]) => step('throw', args),
    [asynchronous ? Symbol.asyncIterator : Symbol.iterator]() {
      return this;
    },
  };
}
/** Define a reusable traced function, including native sync/async generator functions. */
export function span<This, Args extends unknown[], Result>(
  options: SpanOptions,
  fn: (this: This, ...args: Args) => Result,
): (this: This, ...args: Args) => Result {
  validate(options, spanOptions);
  role(options);
  effectivePolicy(options, context.active());
  const saved = {
    ...options,
    ...(options.attributes ? { attributes: { ...options.attributes } } : {}),
  };
  const name = options.name ?? fn.name ?? 'span';
  const isGenerator = types.isGeneratorFunction(fn);
  const asynchronous = types.isAsyncFunction(fn);
  return function (this: This, ...args: Args): Result {
    if (!enabled(saved)) return Reflect.apply(fn, this, args);
    const parent = context.active();
    const factory = () => {
      const op = start(name || 'span', saved, parent);
      if (saved.captureContent !== false) op?.auto('input', args);
      return op;
    };
    if (isGenerator) {
      const iterator = Reflect.apply(fn, this, args) as NativeIterator;
      return generator(
        iterator,
        factory,
        saved.captureContent !== false,
        asynchronous,
      ) as Result;
    }
    return execute(
      factory(),
      () => Reflect.apply(fn, this, args),
      saved.captureContent !== false,
    );
  };
}
