import { context, trace, SpanKind, SpanStatusCode } from '@opentelemetry/api';
import type { Span, TimeInput } from '@opentelemetry/api';
import { isDisabled } from '@/config/resolve';
import { frameworkPolicy, safely } from '@/integrations/framework';
import type { InstrumentationOptions } from '@/integrations/instrument';
import type { IntegrationSpanType } from '@/integrations/span-type';
import type { ContentPolicy } from '@/content/policy';
import { state } from '@/runtime/state';
import { VERSION } from '@/runtime/version';
import * as S from '@/semconv/generated';

export interface CallbackOptions extends InstrumentationOptions {
  /** Maximum simultaneous spans retained by this adapter. Default: 4096. */
  maxActiveSpans?: number;
}
export interface CallbackRun {
  span: Span;
  policy: ContentPolicy;
  root: boolean;
  type: IntegrationSpanType;
}
/** Shared lifecycle; callbacks never change the application's ambient context. */
export class CallbackRuns {
  readonly active = new Map<string, CallbackRun>();
  private closed = false;
  private readonly limit: number;
  constructor(
    readonly integration: string,
    readonly options: CallbackOptions,
  ) {
    frameworkPolicy(options);
    this.limit = options.maxActiveSpans ?? 4096;
    if (!Number.isSafeInteger(this.limit) || this.limit < 1)
      throw new Error('maxActiveSpans must be a positive integer');
  }
  start(
    id: string,
    parentId: string | undefined,
    name: string,
    type: IntegrationSpanType,
    time?: TimeInput,
  ): void {
    safely(() => {
      if (
        this.closed ||
        isDisabled() ||
        this.active.has(id) ||
        this.active.size >= this.limit ||
        (state.runtime && !state.runtime.active && !this.options.tracer)
      )
        return;
      const parent = parentId ? this.active.get(parentId) : undefined;
      if (parentId && !parent) return;
      // Native IDs determine hierarchy; root calls inherit the caller's OTel context.
      const parentContext = parent
        ? trace.setSpan(context.active(), parent.span)
        : context.active();
      const tracer =
        this.options.tracer ??
        trace.getTracerProvider().getTracer('confident-trace', VERSION, {
          schemaUrl: S.SCHEMA_URL,
        });
      const span = tracer.startSpan(
        name,
        {
          kind: type === 'llm' ? SpanKind.CLIENT : SpanKind.INTERNAL,
          ...(time === undefined ? {} : { startTime: time }),
          attributes: {
            [S.ATTR_CONFIDENT_SPAN_INTEGRATION]: this.integration,
            [S.ATTR_CONFIDENT_SPAN_TYPE]: type,
            ...(type === 'llm'
              ? { [S.ATTR_GEN_AI_OPERATION_NAME]: 'chat' }
              : {}),
            ...(type === 'tool'
              ? {
                  [S.ATTR_GEN_AI_OPERATION_NAME]: 'execute_tool',
                  [S.ATTR_GEN_AI_TOOL_NAME]: name,
                }
              : {}),
            ...(type === 'agent'
              ? { [S.ATTR_GEN_AI_OPERATION_NAME]: 'invoke_agent' }
              : {}),
          },
        },
        parentContext,
      );
      this.active.set(id, {
        span,
        type,
        root: !trace.getSpanContext(parentContext),
        policy: frameworkPolicy(this.options),
      });
    });
  }
  content(
    id: string,
    direction: 'input' | 'output',
    value: unknown,
    messages = false,
  ): void {
    const run = this.active.get(id);
    if (!run || value === undefined) return;
    const encoded = run.policy.encode(value);
    if (encoded === undefined) return;
    run.span.setAttribute(
      messages
        ? direction === 'input'
          ? S.ATTR_GEN_AI_INPUT_MESSAGES
          : S.ATTR_GEN_AI_OUTPUT_MESSAGES
        : direction === 'input'
          ? S.ATTR_CONFIDENT_SPAN_INPUT
          : S.ATTR_CONFIDENT_SPAN_OUTPUT,
      encoded,
    );
    if (run.root)
      run.span.setAttribute(
        direction === 'input'
          ? S.ATTR_CONFIDENT_TRACE_INPUT
          : S.ATTR_CONFIDENT_TRACE_OUTPUT,
        encoded,
      );
    if (encoded.includes('[truncated]'))
      run.span.setAttribute(S.ATTR_CONFIDENT_SPAN_CONTENT_TRUNCATED, true);
  }
  attribute(id: string, key: string, value: unknown): void {
    if (
      typeof value === 'string' ||
      (typeof value === 'number' && Number.isFinite(value))
    )
      this.active
        .get(id)
        ?.span.setAttribute(
          key,
          typeof value === 'string' ? value.slice(0, 1024) : value,
        );
  }
  end(id: string, error = false, time?: TimeInput): void {
    const run = this.active.get(id);
    if (!run) return;
    this.active.delete(id);
    safely(() => {
      if (error) {
        run.span.setStatus({ code: SpanStatusCode.ERROR });
        run.span.setAttribute(S.ATTR_ERROR_TYPE, 'framework_error');
      }
      run.span.end(time);
    });
  }
  close(): void {
    this.closed = true;
    for (const id of this.active.keys()) this.end(id, true);
  }
}
