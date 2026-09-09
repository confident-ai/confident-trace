import type { Span } from '@opentelemetry/api';
import type { ContentPolicy } from '@/content/policy';
export interface ThreadFields {
  id?: string;
  tags?: readonly string[];
  metadata?: Record<string, unknown> | null;
}
export interface LlmFields {
  model?: string;
  provider?: string;
  inputTokenCount?: number;
  outputTokenCount?: number;
  /** USD per token, not per million tokens. */
  costPerInputToken?: number;
  costPerOutputToken?: number;
}
export const llmAttributes: Record<string, string> = {
  model: 'gen_ai.request.model',
  provider: 'gen_ai.provider.name',
  inputTokenCount: 'gen_ai.usage.input_tokens',
  outputTokenCount: 'gen_ai.usage.output_tokens',
  costPerInputToken: 'confident.llm.cost_per_input_token',
  costPerOutputToken: 'confident.llm.cost_per_output_token',
};
export function validateFields(fields: object): void {
  const values = fields as Record<string, unknown>;
  const thread = values.thread as ThreadFields | undefined;
  if (thread !== undefined) {
    if (
      !thread ||
      typeof thread !== 'object' ||
      Array.isArray(thread) ||
      Object.keys(thread).some((k) => !['id', 'tags', 'metadata'].includes(k))
    )
      throw new TypeError('thread accepts id, tags, metadata');
    if (thread.id !== undefined && typeof thread.id !== 'string')
      throw new TypeError('thread.id must be a string');
    if (
      thread.id !== undefined &&
      values.threadId !== undefined &&
      thread.id !== values.threadId
    )
      throw new TypeError('Conflicting thread IDs');
  }
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || !llmAttributes[key]) continue;
    if (key === 'model' || key === 'provider') {
      if (typeof value !== 'string')
        throw new TypeError(key + ' must be a string');
    } else if (
      typeof value !== 'number' ||
      !Number.isFinite(value) ||
      value < 0 ||
      (key.endsWith('TokenCount') && !Number.isInteger(value))
    ) {
      throw new TypeError(
        key + ' must be nonnegative and finite; token counts must be integers',
      );
    }
  }
}
export function applyLlmFields(span: Span, fields: LlmFields): void {
  if (!span.isRecording()) return;
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && llmAttributes[key])
      span.setAttribute(llmAttributes[key]!, value);
  }
}
export function applyThreadFields(
  span: Span,
  fields: { thread?: ThreadFields; threadId?: string },
  policy: ContentPolicy,
): void {
  if (!span.isRecording()) return;
  const id = fields.thread?.id ?? fields.threadId;
  if (typeof id === 'string') {
    span.setAttribute('confident.trace.thread.id', id.slice(0, 4096));
    span.setAttribute('confident.trace.thread_id', id.slice(0, 4096));
    span.setAttribute('gen_ai.conversation.id', id.slice(0, 4096));
  }
  const tags = fields.thread?.tags;
  if (Array.isArray(tags) && tags.every((v) => typeof v === 'string'))
    span.setAttribute('confident.trace.thread.tags', tags.slice(0, 128));
  if (fields.thread?.metadata !== undefined) {
    const encoded = policy.encode(fields.thread.metadata);
    if (encoded !== undefined)
      span.setAttribute('confident.trace.thread.metadata', encoded);
  }
}
