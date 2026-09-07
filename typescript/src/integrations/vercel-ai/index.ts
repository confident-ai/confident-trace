import {
  context,
  trace,
  ProxyTracerProvider,
  SpanKind,
  SpanStatusCode,
} from '@opentelemetry/api';
import type { Context, Span, SpanOptions, Tracer } from '@opentelemetry/api';
import type { InstrumentationOptions } from '@/integrations/instrument';
import {
  frameworkMessages,
  semanticMessages,
  semanticParts,
  frameworkPolicy,
  parseContent,
  safely,
} from '@/integrations/framework';
import { get, list } from '@/integrations/extract';
import { vercelSpanType } from '@/integrations/span-type';
import { isDisabled } from '@/config/resolve';
import { state } from '@/runtime/state';
import { VERSION } from '@/runtime/version';
import * as S from '@/semconv/generated';

export type VercelAITracerOptions = InstrumentationOptions;
const noop = new ProxyTracerProvider().getTracer('confident-trace-disabled');
const mappings: Record<string, string> = {
  'ai.model.id': S.ATTR_GEN_AI_REQUEST_MODEL,
  'ai.model.provider': S.ATTR_GEN_AI_PROVIDER_NAME,
  'ai.response.id': S.ATTR_GEN_AI_RESPONSE_ID,
  'ai.response.model': S.ATTR_GEN_AI_RESPONSE_MODEL,
  'ai.usage.promptTokens': S.ATTR_GEN_AI_USAGE_INPUT_TOKENS,
  'ai.usage.inputTokens': S.ATTR_GEN_AI_USAGE_INPUT_TOKENS,
  'ai.usage.completionTokens': S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
  'ai.usage.outputTokens': S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
  'ai.settings.maxTokens': S.ATTR_GEN_AI_REQUEST_MAX_TOKENS,
  'ai.settings.maxOutputTokens': S.ATTR_GEN_AI_REQUEST_MAX_TOKENS,
  'ai.settings.temperature': S.ATTR_GEN_AI_REQUEST_TEMPERATURE,
  'ai.settings.topP': S.ATTR_GEN_AI_REQUEST_TOP_P,
  'ai.settings.stopSequences': S.ATTR_GEN_AI_REQUEST_STOP_SEQUENCES,
  'ai.toolCall.name': S.ATTR_GEN_AI_TOOL_NAME,
  [S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS]:
    S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS,
};

/** Pass this tracer to AI SDK 7 OpenTelemetry (or LegacyOpenTelemetry). */
export function createVercelAITracer(
  options: VercelAITracerOptions = {},
): Tracer {
  frameworkPolicy(options);
  const startSpan = (
    name: string,
    spanOptions: SpanOptions = {},
    parent: Context = context.active(),
  ): Span => {
    if (
      isDisabled() ||
      (state.runtime && !state.runtime.active && !options.tracer)
    )
      return noop.startSpan(name);
    const policy = frameworkPolicy(options);
    const tracer =
      options.tracer ??
      trace
        .getTracerProvider()
        .getTracer('confident-trace', VERSION, { schemaUrl: S.SCHEMA_URL });
    const tool = name === 'ai.toolCall' || name.startsWith('execute_tool ');
    const model =
      /\.(doGenerate|doStream)$/.test(name) || name.startsWith('chat ');
    const entry = !trace.getSpanContext(parent);
    const span = tracer.startSpan(
      name,
      {
        ...spanOptions,
        ...(spanOptions.links
          ? {
              links: spanOptions.links.map((link) => ({
                context: link.context,
              })),
            }
          : {}),
        attributes: {
          [S.ATTR_CONFIDENT_SPAN_INTEGRATION]: S.INTEGRATIONS.vercel_ai,
          [S.ATTR_CONFIDENT_SPAN_TYPE]: vercelSpanType(
            name,
            spanOptions.attributes?.[S.ATTR_GEN_AI_OPERATION_NAME],
          ),
          [S.ATTR_GEN_AI_OPERATION_NAME]: tool
            ? 'execute_tool'
            : model
              ? 'chat'
              : 'invoke_agent',
        },
        kind: model ? SpanKind.CLIENT : SpanKind.INTERNAL,
      },
      parent,
    );
    if (!span.isRecording()) return span;
    let ended = false;
    let outputText: string | undefined;
    let outputTools: unknown;
    const content = (key: string, value: unknown) => {
      const encoded = policy.encode(value);
      if (encoded !== undefined) span.setAttribute(key, encoded);
      if (encoded === '"[truncated]"')
        span.setAttribute(S.ATTR_CONFIDENT_SPAN_CONTENT_TRUNCATED, true);
    };
    const input = (value: unknown) => {
      const normalized = frameworkMessages(value);
      content(S.ATTR_GEN_AI_INPUT_MESSAGES, normalized);
      if (entry) content(S.ATTR_CONFIDENT_TRACE_INPUT, normalized);
    };
    const output = () => {
      const normalized = frameworkMessages(outputText ?? '', 'assistant');
      for (const t of list(outputTools))
        normalized[0]!.parts.push({
          type: 'tool_call',
          ...(typeof get(t, 'toolName') === 'string'
            ? { name: get(t, 'toolName') as string }
            : {}),
          ...(typeof get(t, 'toolCallId') === 'string'
            ? { id: get(t, 'toolCallId') as string }
            : {}),
          arguments: get(t, 'input') ?? get(t, 'args'),
        });
      content(S.ATTR_GEN_AI_OUTPUT_MESSAGES, normalized);
      if (entry) content(S.ATTR_CONFIDENT_TRACE_OUTPUT, normalized);
    };
    const set = (key: string, value: unknown) => {
      if (ended || value === undefined) return;
      const mapped =
        mappings[key] ??
        (Object.values(mappings).includes(key) ||
        key === S.ATTR_GEN_AI_OPERATION_NAME
          ? key
          : undefined);
      if (mapped) {
        if (
          typeof value === 'string' ||
          (typeof value === 'number' && Number.isFinite(value))
        )
          span.setAttribute(mapped, value);
        else if (Array.isArray(value))
          span.setAttribute(
            mapped,
            list(value).filter((v): v is string => typeof v === 'string'),
          );
        return;
      }
      if (
        (key === 'ai.response.finishReason' ||
          key === S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS) &&
        typeof value === 'string'
      ) {
        span.setAttribute(S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS, [value]);
        return;
      }
      // Keep only explicitly understood attributes. SDK prompt/tool/metadata fields
      // may contain secrets; never pass their raw values to an OTel processor.
      if (!policy.enabled) return;
      if (
        [
          S.ATTR_GEN_AI_INPUT_MESSAGES,
          S.ATTR_GEN_AI_OUTPUT_MESSAGES,
          S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS,
        ].includes(key as typeof S.ATTR_GEN_AI_INPUT_MESSAGES)
      ) {
        const parsed =
          key === S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS
            ? semanticParts(parseContent(value, policy))
            : semanticMessages(parseContent(value, policy));
        content(key, parsed);
        if (entry && key === S.ATTR_GEN_AI_INPUT_MESSAGES)
          content(S.ATTR_CONFIDENT_TRACE_INPUT, parsed);
        if (entry && key === S.ATTR_GEN_AI_OUTPUT_MESSAGES)
          content(S.ATTR_CONFIDENT_TRACE_OUTPUT, parsed);
      } else if (key === 'ai.prompt') {
        const prompt = parseContent(value, policy);
        input(get(prompt, 'messages') ?? get(prompt, 'prompt') ?? prompt);
        if (get(prompt, 'system') !== undefined)
          content(S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS, [
            { type: 'text', content: get(prompt, 'system') },
          ]);
      } else if (key === 'ai.prompt.messages')
        input(parseContent(value, policy));
      else if (key === 'ai.response.text' && typeof value === 'string') {
        outputText = value.slice(0, policy.maxBytes);
        if (outputText.length < value.length)
          span.setAttribute(S.ATTR_CONFIDENT_SPAN_CONTENT_TRUNCATED, true);
        output();
      } else if (key === 'ai.response.toolCalls') {
        outputTools = parseContent(value, policy);
        output();
      } else if (key === 'ai.response.object') {
        content(S.ATTR_CONFIDENT_SPAN_OUTPUT, parseContent(value, policy));
        if (entry)
          content(S.ATTR_CONFIDENT_TRACE_OUTPUT, parseContent(value, policy));
      } else if (
        key === 'ai.toolCall.args' ||
        key === 'ai.toolCall.input' ||
        key === 'gen_ai.tool.call.arguments'
      )
        content(S.ATTR_CONFIDENT_SPAN_INPUT, parseContent(value, policy));
      else if (
        key === 'ai.toolCall.result' ||
        key === 'gen_ai.tool.call.result'
      )
        content(S.ATTR_CONFIDENT_SPAN_OUTPUT, parseContent(value, policy));
    };
    const facade: Span = {
      spanContext: () => span.spanContext(),
      isRecording: () => !ended && span.isRecording(),
      setAttribute(key, value) {
        safely(() => set(key, value));
        return this;
      },
      setAttributes(attributes) {
        for (const key of Object.keys(attributes))
          safely(() => set(key, attributes[key]));
        return this;
      },
      setStatus(status) {
        safely(() => span.setStatus({ code: status.code }));
        return this;
      },
      recordException() {
        safely(() => {
          span.setStatus({ code: SpanStatusCode.ERROR });
          span.setAttribute(S.ATTR_ERROR_TYPE, 'framework_error');
        });
      },
      addEvent(name, attributesOrTime, time) {
        // Retain SDK timing events without arbitrary payloads or exception text.
        if (name === 'ai.stream.firstChunk' || name === 'ai.stream.finish')
          safely(() =>
            span.addEvent(
              name,
              {},
              time ??
                (typeof attributesOrTime === 'number'
                  ? attributesOrTime
                  : undefined),
            ),
          );
        return this;
      },
      addLink(link) {
        safely(() => span.addLink({ context: link.context }));
        return this;
      },
      addLinks(links) {
        for (const link of links) this.addLink(link);
        return this;
      },
      updateName(name) {
        safely(() => span.updateName(name));
        return this;
      },
      end(time) {
        if (ended) return;
        ended = true;
        outputText = undefined;
        outputTools = undefined;
        safely(() => span.end(time));
      },
    };
    facade.setAttributes(spanOptions.attributes ?? {});
    return facade;
  };
  const tracer: Tracer = {
    startSpan,
    startActiveSpan: ((name: string, ...args: unknown[]) => {
      const fn = args.at(-1) as (span: Span) => unknown;
      const spanOptions = args.length > 1 ? (args[0] as SpanOptions) : {};
      const parent = args.length > 2 ? (args[1] as Context) : context.active();
      const span = startSpan(name, spanOptions, parent);
      return context.with(trace.setSpan(parent, span), fn, undefined, span);
    }) as Tracer['startActiveSpan'],
  };
  return tracer;
}
