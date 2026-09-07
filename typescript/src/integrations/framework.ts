import { ContentPolicy } from '@/content/policy';
import type { ContentOptions } from '@/content/types';
import { state } from '@/runtime/state';
import { get, list } from '@/integrations/extract';
import type { GenAiMessage, GenAiPart } from '@/semconv/messages';

export function frameworkPolicy(options: ContentOptions): ContentPolicy {
  return state.policy?.withOptions(options) ?? new ContentPolicy(options);
}
export function safely(action: () => void): void {
  try {
    action();
  } catch {
    /* Telemetry failures must not affect framework execution. */
  }
}
/** Normalize the text/tool subset used by AI SDK and Mastra. Never inspect binary parts. */
export function frameworkMessages(
  value: unknown,
  role = 'user',
): GenAiMessage[] {
  if (typeof value === 'string')
    return [{ role, parts: [{ type: 'text', content: value }] }];
  return list(value).map((message) => {
    const r = get(message, 'role');
    const content = get(message, 'content');
    const parts: GenAiPart[] =
      typeof content === 'string'
        ? [{ type: 'text', content }]
        : list(content).map((part) => {
            if (
              get(part, 'type') === 'text' &&
              typeof get(part, 'text') === 'string'
            )
              return { type: 'text', content: get(part, 'text') as string };
            if (get(part, 'type') === 'tool-call')
              return {
                type: 'tool_call',
                ...(typeof get(part, 'toolName') === 'string'
                  ? { name: get(part, 'toolName') as string }
                  : {}),
                ...(typeof get(part, 'toolCallId') === 'string'
                  ? { id: get(part, 'toolCallId') as string }
                  : {}),
                arguments: get(part, 'input') ?? get(part, 'args'),
              };
            return { type: 'text', content: '[unsupported content]' };
          });
    return { role: typeof r === 'string' ? r : role, parts };
  });
}
export function parseContent(value: unknown, policy: ContentPolicy): unknown {
  if (typeof value !== 'string') return value;
  // Bound work before parsing SDK-generated JSON. The SDK owns its original buffer.
  if (
    value.length > policy.maxBytes ||
    Buffer.byteLength(value) > policy.maxBytes
  )
    return '[truncated]';
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

/** Keep the selected 1.37 message subset when an upstream tracer advances. */
export function semanticMessages(value: unknown): unknown {
  if (typeof value === 'string') return value;
  return list(value).map((message) => ({
    role:
      typeof get(message, 'role') === 'string'
        ? get(message, 'role')
        : 'assistant',
    parts: semanticParts(get(message, 'parts')),
  }));
}

export function semanticParts(value: unknown): unknown {
  if (typeof value === 'string') return value;
  return list(value).map((part) => {
    if (
      get(part, 'type') === 'text' &&
      typeof get(part, 'content') === 'string'
    )
      return { type: 'text', content: get(part, 'content') };
    if (get(part, 'type') === 'tool_call')
      return {
        type: 'tool_call',
        ...(typeof get(part, 'id') === 'string' ? { id: get(part, 'id') } : {}),
        ...(typeof get(part, 'name') === 'string'
          ? { name: get(part, 'name') }
          : {}),
        arguments: get(part, 'arguments'),
      };
    return { type: 'text', content: '[unsupported content]' };
  });
}
export function frameworkOutput(value: unknown): GenAiMessage[] {
  const raw = get(value, 'messages') ?? get(value, 'text') ?? value;
  const messages = frameworkMessages(raw, 'assistant');
  const tools = list(get(value, 'toolCalls'));
  if (tools.length) {
    const calls = frameworkMessages([
      {
        role: 'assistant',
        content: tools.map((tool) => ({
          type: 'tool-call',
          toolName: get(tool, 'toolName'),
          toolCallId: get(tool, 'toolCallId'),
          input: get(tool, 'input') ?? get(tool, 'args'),
        })),
      },
    ]);
    messages.push(...calls);
  }
  return messages;
}
