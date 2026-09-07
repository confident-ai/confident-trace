import { CallbackRuns } from '@/integrations/callback-runs';
import type { CallbackOptions } from '@/integrations/callback-runs';
import type { IntegrationSpanType } from '@/integrations/span-type';
import { frameworkMessages, safely } from '@/integrations/framework';
import { get, list } from '@/integrations/extract';
import { state } from '@/runtime/state';
import * as S from '@/semconv/generated';

export interface OpenAIAgentsProcessorOptions extends CallbackOptions {
  /** Supply when using an application-owned OTel provider. Defaults to the Confident runtime flush. */
  flush?: () => Promise<void>;
}
/** Structural types keep optional SDK packages out of consumer declarations. */
export interface AgentsTrace {
  readonly traceId: string;
  readonly name: string;
}
export interface AgentsSpan {
  readonly traceId: string;
  readonly spanId: string;
  readonly parentId: string | null;
  readonly spanData: unknown;
  readonly startedAt: string | null;
  readonly endedAt: string | null;
  readonly error: unknown;
}
function timestamp(value: string | null): Date | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : undefined;
}
function role(type: unknown): IntegrationSpanType {
  if (type === 'agent') return 'agent';
  if (
    type === 'generation' ||
    type === 'response' ||
    type === 'speech' ||
    type === 'transcription'
  )
    return 'llm';
  if (type === 'function') return 'tool';
  return 'custom';
}
function messages(value: unknown, fallback: string): unknown {
  if (typeof value === 'string') return frameworkMessages(value, fallback);
  return list(value).flatMap<unknown>((item) => {
    const type = get(item, 'type');
    if (type === 'function_call')
      return [
        {
          role: 'assistant',
          parts: [
            {
              type: 'tool_call',
              name: get(item, 'name'),
              id: get(item, 'call_id'),
              arguments: get(item, 'arguments'),
            },
          ],
        },
      ];
    if (type === 'function_call_output')
      return frameworkMessages([
        { role: 'tool', content: get(item, 'output') },
      ]);
    const content = get(item, 'content');
    return frameworkMessages([
      {
        role: get(item, 'role') ?? fallback,
        content:
          typeof content === 'string'
            ? content
            : list(content).map((part) => ({
                type: ['input_text', 'output_text', 'text'].includes(
                  String(get(part, 'type')),
                )
                  ? 'text'
                  : 'unsupported',
                text: get(part, 'text'),
              })),
      },
    ]);
  });
}

/** Register with setTraceProcessors or addTraceProcessor from @openai/agents. */
export class ConfidentOpenAIAgentsProcessor {
  private readonly runs: CallbackRuns;
  private shutdownPromise?: Promise<void>;
  constructor(private readonly options: OpenAIAgentsProcessorOptions = {}) {
    this.runs = new CallbackRuns(S.INTEGRATIONS.openai_agents, options);
  }
  async onTraceStart(source: AgentsTrace): Promise<void> {
    safely(() =>
      this.runs.start(source.traceId, undefined, source.name, 'custom'),
    );
  }
  async onTraceEnd(source: AgentsTrace): Promise<void> {
    safely(() => this.runs.end(source.traceId));
  }
  async onSpanStart(source: AgentsSpan): Promise<void> {
    safely(() => {
      const data = source.spanData;
      const type = get(data, 'type');
      const name = get(data, 'name');
      this.runs.start(
        `${source.traceId}/${source.spanId}`,
        source.parentId
          ? `${source.traceId}/${source.parentId}`
          : source.traceId,
        typeof name === 'string'
          ? name
          : typeof type === 'string'
            ? type
            : 'span',
        role(type),
        timestamp(source.startedAt),
      );
    });
  }
  async onSpanEnd(source: AgentsSpan): Promise<void> {
    const id = `${source.traceId}/${source.spanId}`;
    if (!this.runs.active.has(id)) return;
    safely(() => {
      const data = source.spanData;
      const type = get(data, 'type');
      const response = get(data, '_response');
      this.runs.attribute(id, 'openai.agents.span.type', type);
      this.runs.attribute(id, S.ATTR_GEN_AI_REQUEST_MODEL, get(data, 'model'));
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_RESPONSE_MODEL,
        get(response, 'model'),
      );
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_RESPONSE_ID,
        get(data, 'response_id') ?? get(response, 'id'),
      );
      const usage = get(data, 'usage') ?? get(response, 'usage');
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_USAGE_INPUT_TOKENS,
        get(usage, 'input_tokens'),
      );
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
        get(usage, 'output_tokens'),
      );
      if (type === 'response')
        this.runs.attribute(id, S.ATTR_GEN_AI_PROVIDER_NAME, 'openai');
      if (type === 'generation' || type === 'response') {
        this.runs.content(
          id,
          'input',
          messages(get(data, 'input') ?? get(data, '_input'), 'user'),
          true,
        );
        this.runs.content(
          id,
          'output',
          messages(get(data, 'output') ?? get(response, 'output'), 'assistant'),
          true,
        );
      } else if (type === 'function') {
        this.runs.content(id, 'input', get(data, 'input'));
        this.runs.content(id, 'output', get(data, 'output'));
      } else if (type === 'custom')
        this.runs.content(id, 'output', get(data, 'data'));
      // Audio, credentials, arbitrary metadata, and exception payloads are never copied.
    });
    this.runs.end(id, Boolean(source.error), timestamp(source.endedAt));
  }
  async forceFlush(): Promise<void> {
    if (this.options.flush) await this.options.flush();
    else if (state.runtime && !(await state.runtime.flush()))
      throw new Error('Trace flush failed');
  }
  shutdown(): Promise<void> {
    if (!this.shutdownPromise) {
      this.runs.close();
      this.shutdownPromise = this.forceFlush();
    }
    return this.shutdownPromise;
  }
}
