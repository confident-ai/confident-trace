import {
  diag,
  isSpanContextValid,
  SpanKind,
  SpanStatusCode,
  TraceFlags,
} from '@opentelemetry/api';
import type { Attributes, HrTime, SpanContext } from '@opentelemetry/api';
import {
  defaultResource,
  detectResources,
  envDetector,
  resourceFromAttributes,
} from '@opentelemetry/resources';
import type { Resource } from '@opentelemetry/resources';
import type {
  ReadableSpan,
  SpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import type { InitOptions } from '@/config/types';
import { isDisabled } from '@/config/resolve';
import { createSpanProcessor } from '@/runtime/processor';
import { withinBudget } from '@/runtime/lifecycle';
import { VERSION } from '@/runtime/version';
import {
  frameworkMessages,
  frameworkOutput,
  frameworkPolicy,
} from '@/integrations/framework';
import { get, list } from '@/integrations/extract';
import { mastraSpanType } from '@/integrations/span-type';
import * as S from '@/semconv/generated';

/** Structural subset of Mastra's public TracingEvent; no SDK runtime dependency. */
export interface MastraTracingEvent {
  type: string;
  exportedSpan: {
    id: string;
    traceId: string;
    name: string;
    type: string;
    startTime: Date;
    endTime?: Date;
    parentSpanId?: string;
    externalParentSpanId?: string;
    isRootSpan: boolean;
    isEvent: boolean;
    attributes?: unknown;
    input?: unknown;
    output?: unknown;
    errorInfo?: unknown;
    metadata?: unknown;
    tags?: string[];
  };
}
export type MastraExporterOptions = InitOptions;
function hrTime(ms: number): HrTime {
  const seconds = Math.floor(ms / 1000);
  return [seconds, Math.round((ms - seconds * 1000) * 1e6)];
}

/** Mastra exporter with its own OTLP processor. Mastra owns its flush/shutdown. */
export class ConfidentMastraExporter {
  readonly name = 'confident-trace';
  private readonly processor: SpanProcessor;
  private resource: Resource;
  private closed = false;
  private closing: Promise<void> | undefined;
  constructor(private readonly options: MastraExporterOptions = {}) {
    frameworkPolicy(options); // Validate before taking ownership of an exporter.
    this.resource = defaultResource()
      .merge(detectResources({ detectors: [envDetector] }))
      .merge(resourceFromAttributes(options.resourceAttributes ?? {}));
    this.processor = createSpanProcessor(options);
  }
  init(options: { config?: { serviceName?: string } }): void {
    const serviceName = options.config?.serviceName;
    if (
      serviceName &&
      this.options.resourceAttributes?.['service.name'] === undefined
    )
      this.resource = this.resource.merge(
        resourceFromAttributes({ 'service.name': serviceName }),
      );
  }
  async exportTracingEvent(event: MastraTracingEvent): Promise<void> {
    if (this.closed || isDisabled() || event.type !== 'span_ended') return;
    try {
      const source = event.exportedSpan;
      const spanContext: SpanContext = {
        traceId: source.traceId,
        spanId: source.id,
        traceFlags: TraceFlags.SAMPLED,
      };
      if (!isSpanContextValid(spanContext)) return;
      const parentId = source.parentSpanId ?? source.externalParentSpanId;
      const parent = parentId
        ? { ...spanContext, spanId: parentId }
        : undefined;
      if (parent && !isSpanContextValid(parent)) return;
      const start = source.startTime.getTime();
      const end = (
        source.endTime ?? (source.isEvent ? source.startTime : undefined)
      )?.getTime();
      if (
        !Number.isFinite(start) ||
        end === undefined ||
        !Number.isFinite(end) ||
        end < start
      )
        return;
      const attributes: Attributes = {
        [S.ATTR_CONFIDENT_SPAN_INTEGRATION]: S.INTEGRATIONS.mastra,
        [S.ATTR_CONFIDENT_SPAN_TYPE]: mastraSpanType(source.type),
        'mastra.span.type': source.type,
      };
      const data = source.attributes;
      const policy = frameworkPolicy(this.options);
      const set = (key: string, value: unknown) => {
        if (
          typeof value === 'string' ||
          (typeof value === 'number' && Number.isFinite(value))
        )
          attributes[key] = value;
      };
      const content = (key: string, value: unknown) => {
        if (value === undefined) return;
        const encoded = policy.encode(value);
        if (encoded !== undefined) attributes[key] = encoded;
      };
      const model = [
        'model_generation',
        'model_step',
        'model_inference',
      ].includes(source.type);
      const tool = [
        'tool_call',
        'mcp_tool_call',
        'client_tool_call',
        'provider_tool_call',
      ].includes(source.type);
      if (model) attributes[S.ATTR_GEN_AI_OPERATION_NAME] = 'chat';
      else if (tool) {
        attributes[S.ATTR_GEN_AI_OPERATION_NAME] = 'execute_tool';
        set(
          S.ATTR_GEN_AI_TOOL_NAME,
          get(data, 'toolName') ?? get(data, 'name') ?? source.name,
        );
      }
      for (const [key, attr] of [
        ['model', S.ATTR_GEN_AI_REQUEST_MODEL],
        ['provider', S.ATTR_GEN_AI_PROVIDER_NAME],
        ['responseModel', S.ATTR_GEN_AI_RESPONSE_MODEL],
        ['responseId', S.ATTR_GEN_AI_RESPONSE_ID],
      ] as const)
        set(attr, get(data, key));
      const usage = get(data, 'usage');
      set(S.ATTR_GEN_AI_USAGE_INPUT_TOKENS, get(usage, 'inputTokens'));
      set(S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS, get(usage, 'outputTokens'));
      const finish = get(data, 'finishReason');
      if (typeof finish === 'string')
        attributes[S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS] = [finish];
      const parameters = get(data, 'parameters');
      for (const [key, attr] of [
        ['temperature', S.ATTR_GEN_AI_REQUEST_TEMPERATURE],
        ['topP', S.ATTR_GEN_AI_REQUEST_TOP_P],
        ['maxOutputTokens', S.ATTR_GEN_AI_REQUEST_MAX_TOKENS],
      ] as const)
        set(attr, get(parameters, key));
      const stops = get(parameters, 'stopSequences');
      if (Array.isArray(stops))
        attributes[S.ATTR_GEN_AI_REQUEST_STOP_SEQUENCES] = list(stops).filter(
          (v): v is string => typeof v === 'string',
        );
      const messageSpan = model || source.type === 'agent_run';
      if (policy.enabled) {
        if (messageSpan) {
          const system = get(source.input, 'system');
          if (typeof system === 'string')
            content(S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS, [
              { type: 'text', content: system },
            ]);
          if (source.input !== undefined)
            content(
              S.ATTR_GEN_AI_INPUT_MESSAGES,
              frameworkMessages(get(source.input, 'messages') ?? source.input),
            );
          if (source.output !== undefined) {
            content(
              S.ATTR_GEN_AI_OUTPUT_MESSAGES,
              frameworkOutput(source.output),
            );
          }
        } else {
          content(S.ATTR_CONFIDENT_SPAN_INPUT, source.input);
          content(S.ATTR_CONFIDENT_SPAN_OUTPUT, source.output);
        }
        if (source.isRootSpan) {
          if (source.input !== undefined)
            content(
              S.ATTR_CONFIDENT_TRACE_INPUT,
              messageSpan
                ? frameworkMessages(
                    get(source.input, 'messages') ?? source.input,
                  )
                : source.input,
            );
          if (source.output !== undefined)
            content(
              S.ATTR_CONFIDENT_TRACE_OUTPUT,
              messageSpan ? frameworkOutput(source.output) : source.output,
            );
          content(S.ATTR_CONFIDENT_TRACE_METADATA, source.metadata);
        }
      }
      if (source.isRootSpan) {
        set(S.ATTR_CONFIDENT_TRACE_NAME, source.name);
        if (source.tags)
          attributes[S.ATTR_CONFIDENT_TRACE_TAGS] = list(source.tags).filter(
            (v): v is string => typeof v === 'string',
          );
      }
      if (source.errorInfo != null)
        attributes[S.ATTR_ERROR_TYPE] = 'framework_error';
      const span: ReadableSpan = {
        name: source.name,
        kind:
          source.type === 'model_inference' ||
          source.type === 'model_generation'
            ? SpanKind.CLIENT
            : SpanKind.INTERNAL,
        spanContext: () => spanContext,
        ...(parent ? { parentSpanContext: parent } : {}),
        startTime: hrTime(start),
        endTime: hrTime(end),
        duration: hrTime(end - start),
        status: {
          code:
            source.errorInfo != null
              ? SpanStatusCode.ERROR
              : SpanStatusCode.UNSET,
        },
        attributes,
        links: [],
        events: [],
        ended: true,
        resource: this.resource,
        instrumentationScope: {
          name: 'confident-trace',
          version: VERSION,
          schemaUrl: S.SCHEMA_URL,
        },
        droppedAttributesCount: 0,
        droppedEventsCount: 0,
        droppedLinksCount: 0,
      };
      this.processor.onEnd(span);
    } catch {
      diag.debug('Confident Trace could not export a Mastra span');
    }
  }
  onTracingEvent(event: MastraTracingEvent): Promise<void> {
    return this.exportTracingEvent(event);
  }
  async flush(): Promise<void> {
    const ok = await withinBudget(
      () => this.closing ?? this.processor.forceFlush(),
      this.options.timeoutMillis ?? 30000,
    );
    if (!ok)
      throw new Error('Confident Trace Mastra flush failed or timed out');
  }
  shutdown(): Promise<void> {
    if (!this.closing) {
      this.closed = true;
      this.closing = (async () => {
        const ok = await withinBudget(
          () => this.processor.shutdown(),
          this.options.timeoutMillis ?? 5000,
        );
        if (!ok)
          throw new Error(
            'Confident Trace Mastra shutdown failed or timed out',
          );
      })();
    }
    return this.closing;
  }
}
