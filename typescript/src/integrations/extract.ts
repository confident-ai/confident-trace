import { types } from 'node:util';
import type { Span, AttributeValue } from '@opentelemetry/api';
import { ContentPolicy } from '@/content/policy';
import type { GenAiMessage, GenAiPart } from '@/semconv/messages';
import * as S from '@/semconv/generated';

export type Provider =
  'openai' | 'anthropic' | 'google_genai' | 'openrouter' | 'portkey';
export function get(value: unknown, key: string): unknown {
  if (!value || typeof value !== 'object' || types.isProxy(value))
    return undefined;
  const camelKey = key.replace(/_([a-z])/g, (_, letter: string) =>
    letter.toUpperCase(),
  );
  const d =
    Object.getOwnPropertyDescriptor(value, key) ??
    Object.getOwnPropertyDescriptor(value, camelKey);
  return d && 'value' in d ? d.value : undefined;
}
export function list(value: unknown): unknown[] {
  if (!Array.isArray(value) || types.isProxy(value)) return [];
  const result: unknown[] = [];
  for (let i = 0; i < Math.min(value.length, 128); i++)
    result.push(get(value, String(i)));
  return result;
}
function text(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined;
}
function tool(value: unknown, google = false): GenAiPart {
  const name = text(get(value, 'name'));
  const id = text(get(value, 'call_id') ?? get(value, 'id'));
  return {
    type: 'tool_call',
    ...(name === undefined ? {} : { name }),
    ...(id === undefined ? {} : { id }),
    arguments: get(value, google ? 'args' : 'arguments') ?? get(value, 'input'),
  };
}
export function parts(value: unknown): GenAiPart[] {
  if (typeof value === 'string') return [{ type: 'text', content: value }];
  return list(value).map((block) => {
    const t = get(block, 'text');
    if (typeof t === 'string') return { type: 'text', content: t };
    if (
      get(block, 'type') === 'tool_use' ||
      get(block, 'type') === 'function_call'
    )
      return tool(block);
    if (get(block, 'functionCall'))
      return tool(get(block, 'functionCall'), true);
    return { type: 'text', content: '[unsupported content]' };
  });
}
export function messages(value: unknown): GenAiMessage[] {
  if (typeof value === 'string') return [{ role: 'user', parts: parts(value) }];
  return (Array.isArray(value) ? list(value) : [value]).map((m) => {
    if (typeof m === 'string') return { role: 'user', parts: parts(m) };
    if (get(m, 'type') === 'function_call')
      return { role: 'assistant', parts: [tool(m)] };
    const role = text(get(m, 'role')) ?? 'user';
    const p = parts(get(m, 'content') ?? get(m, 'parts') ?? '');
    for (const t of list(get(m, 'tool_calls'))) {
      const fn = get(t, 'function');
      p.push(
        tool({
          name: get(fn, 'name'),
          id: get(t, 'id'),
          arguments: get(fn, 'arguments'),
        }),
      );
    }
    return { role: role === 'model' ? 'assistant' : role, parts: p };
  });
}
export class Capture {
  private retained: GenAiMessage[] = [];
  private used = 0;
  private full = false;
  private tools = new Map<
    string,
    { type: 'tool_call'; name: string; id: string; arguments: string }
  >();
  constructor(
    readonly span: Span,
    readonly policy: ContentPolicy,
    readonly provider: Provider,
    readonly entry: boolean,
  ) {}
  set(key: string, value: unknown) {
    if (Array.isArray(value)) value = list(value);
    if (
      typeof value === 'string' ||
      (typeof value === 'number' && Number.isFinite(value)) ||
      typeof value === 'boolean' ||
      (Array.isArray(value) && value.every((v) => typeof v === 'string'))
    )
      this.span.setAttribute(key, value as AttributeValue);
  }
  content(key: string, value: unknown) {
    const encoded = this.policy.encode(value);
    if (encoded !== undefined) this.span.setAttribute(key, encoded);
  }
  output(value: GenAiMessage[]) {
    if (!value.length) return;
    this.content(S.ATTR_GEN_AI_OUTPUT_MESSAGES, value);
    if (this.entry) this.content(S.ATTR_CONFIDENT_TRACE_OUTPUT, value);
  }
  request(value: unknown) {
    this.set(S.ATTR_GEN_AI_REQUEST_MODEL, get(value, 'model'));
    const config =
      this.provider === 'google_genai' ? get(value, 'config') : value;
    for (const [attr, keys] of [
      [S.ATTR_GEN_AI_REQUEST_TEMPERATURE, ['temperature']],
      [S.ATTR_GEN_AI_REQUEST_TOP_P, ['top_p', 'topP']],
      [
        S.ATTR_GEN_AI_REQUEST_MAX_TOKENS,
        [
          'max_completion_tokens',
          'max_output_tokens',
          'max_tokens',
          'maxOutputTokens',
        ],
      ],
      [
        S.ATTR_GEN_AI_REQUEST_STOP_SEQUENCES,
        ['stop', 'stop_sequences', 'stopSequences'],
      ],
    ] as const)
      for (const key of keys) {
        const v = get(config, key);
        if (v !== undefined) {
          this.set(attr, v);
          break;
        }
      }
    if (!this.policy.enabled) return;
    const raw =
      get(value, 'messages') ??
      get(value, 'input') ??
      get(value, 'contents') ??
      [];
    // Google also accepts a list of Part objects as a single user content.
    const input =
      this.provider === 'google_genai' &&
      Array.isArray(raw) &&
      get(list(raw)[0], 'role') === undefined &&
      typeof list(raw)[0] !== 'string'
        ? [{ role: 'user', parts: parts(raw) }]
        : messages(raw);
    this.content(S.ATTR_GEN_AI_INPUT_MESSAGES, input);
    if (this.entry) this.content(S.ATTR_CONFIDENT_TRACE_INPUT, input);
    const system =
      get(value, 'system') ??
      get(value, 'instructions') ??
      get(config, 'systemInstruction');
    if (system !== undefined)
      this.content(
        S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS,
        parts(get(system, 'parts') ?? system),
      );
  }
  response(value: unknown, captureOutput = true) {
    if (
      (this.provider === 'openrouter' || this.provider === 'portkey') &&
      get(value, 'error')
    )
      this.span.setStatus({ code: 2 });
    this.set(
      S.ATTR_GEN_AI_RESPONSE_ID,
      get(value, 'id') ?? get(value, 'responseId'),
    );
    this.set(
      S.ATTR_GEN_AI_RESPONSE_MODEL,
      get(value, 'model') ?? get(value, 'modelVersion'),
    );
    const usage = get(value, 'usage') ?? get(value, 'usageMetadata');
    this.set(
      S.ATTR_GEN_AI_USAGE_INPUT_TOKENS,
      get(usage, 'input_tokens') ??
        get(usage, 'prompt_tokens') ??
        get(usage, 'promptTokenCount'),
    );
    this.set(
      S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
      get(usage, 'output_tokens') ??
        get(usage, 'completion_tokens') ??
        get(usage, 'candidatesTokenCount'),
    );
    const choices = list(get(value, 'choices') ?? get(value, 'candidates'));
    const reasons = [
      ...choices.map((c) => get(c, 'finish_reason') ?? get(c, 'finishReason')),
      get(value, 'stop_reason'),
    ].filter((r): r is string => typeof r === 'string');
    if (reasons.length)
      this.set(S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS, reasons);
    if (!this.policy.enabled || !captureOutput) return;
    if (this.provider === 'anthropic' && get(value, 'content') !== undefined)
      this.output([{ role: 'assistant', parts: parts(get(value, 'content')) }]);
    else if (
      (this.provider === 'openai' || this.provider === 'portkey') &&
      get(value, 'output') !== undefined
    )
      this.output(messages(get(value, 'output')));
    else
      this.output(
        choices.flatMap((c) => {
          const m = get(c, 'message') ?? get(c, 'content');
          return m ? messages([m]) : [];
        }),
      );
  }
  private append(value: unknown): string {
    if (typeof value !== 'string' || this.full) return '';
    const room = Math.max(0, Math.floor(this.policy.maxBytes / 6) - this.used);
    const prefix = value.slice(0, room);
    this.used += prefix.length;
    if (prefix.length < value.length) {
      this.full = true;
      this.set(S.ATTR_CONFIDENT_SPAN_CONTENT_TRUNCATED, true);
    }
    return prefix;
  }
  private deltaTool(key: string, value: unknown) {
    if (!this.tools.has(key)) {
      if (this.tools.size >= 32 || this.full) return;
      this.tools.set(key, {
        type: 'tool_call',
        name: '',
        id: '',
        arguments: '',
      });
    }
    const t = this.tools.get(key)!;
    for (const field of ['name', 'id', 'arguments'] as const)
      t[field] += this.append(get(value, field));
  }
  chunk(value: unknown) {
    const nested = get(value, 'response') ?? get(value, 'message');
    this.response(nested ?? value, false);
    const delta = get(value, 'delta');
    if (get(delta, 'stop_reason'))
      this.set(S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS, [
        get(delta, 'stop_reason'),
      ]);
    const event = get(value, 'type');
    if (event === 'error' || event === 'response.failed')
      this.span.setStatus({ code: 2 });
    if (!this.policy.enabled || this.full) return;
    const texts: unknown[] = [];
    if (typeof delta === 'string' && event === 'response.output_text.delta')
      texts.push(delta);
    else texts.push(get(delta, 'text'));
    for (const c of list(get(value, 'choices'))) {
      const d = get(c, 'delta');
      texts.push(get(d, 'content'));
      for (const t of list(get(d, 'tool_calls')))
        this.deltaTool(
          `o:${String(get(c, 'index'))}:${String(get(t, 'index'))}`,
          {
            id: get(t, 'id'),
            name: get(get(t, 'function'), 'name'),
            arguments: get(get(t, 'function'), 'arguments'),
          },
        );
    }
    for (const c of list(get(value, 'candidates')))
      for (const p of list(get(get(c, 'content'), 'parts'))) {
        texts.push(get(p, 'text'));
        if (get(p, 'functionCall')) {
          // Encode each complete Google tool call under the same retention budget.
          const encoded = new ContentPolicy({
            maxContentBytes: this.policy.maxBytes,
          }).encode(tool(get(p, 'functionCall'), true));
          if (
            encoded &&
            encoded !== '"[truncated]"' &&
            this.append(encoded) === encoded
          )
            this.retained.push({
              role: 'assistant',
              parts: [JSON.parse(encoded) as GenAiPart],
            });
        }
      }
    const block = get(value, 'content_block');
    if (get(block, 'type') === 'text') texts.push(get(block, 'text'));
    if (get(block, 'type') === 'tool_use')
      this.deltaTool(`a:${String(get(value, 'index'))}`, {
        id: get(block, 'id'),
        name: get(block, 'name'),
      });
    if (get(delta, 'partial_json'))
      this.deltaTool(`a:${String(get(value, 'index'))}`, {
        arguments: get(delta, 'partial_json'),
      });
    const item = get(value, 'item');
    if (
      event === 'response.output_item.added' &&
      get(item, 'type') === 'function_call'
    )
      this.deltaTool(`r:${String(get(value, 'output_index'))}`, {
        id: get(item, 'call_id'),
        name: get(item, 'name'),
        arguments: get(item, 'arguments'),
      });
    if (event === 'response.function_call_arguments.delta')
      this.deltaTool(`r:${String(get(value, 'output_index'))}`, {
        arguments: delta,
      });
    for (const t of texts) {
      const s = this.append(t);
      if (!s) continue;
      const last = this.retained.at(-1)?.parts[0];
      if (last?.type === 'text') last.content += s;
      else
        this.retained.push({
          role: 'assistant',
          parts: [{ type: 'text', content: s }],
        });
    }
    const output = [...this.retained];
    if (this.tools.size)
      output.push({ role: 'assistant', parts: [...this.tools.values()] });
    this.output(output);
  }
}
