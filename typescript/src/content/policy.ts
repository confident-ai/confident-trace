import { types } from 'node:util';
import { normalizeFrameworkOutput } from '@/content/normalize';
import { Media } from '@/content/media';
import type { MediaPart } from '@/content/media';
import type { ContentOptions } from '@/content/types';
import * as S from '@/semconv/generated';

const MEDIA_TYPES = ['blob', 'uri'];
const MEDIA_OVERHEAD = 256;
const SHAPES: Record<string, string> = {
  [S.ATTR_GEN_AI_INPUT_MESSAGES]: 'input-messages',
  [S.ATTR_GEN_AI_OUTPUT_MESSAGES]: 'output-messages',
  [S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS]: 'system-instructions',
};

/** The message shape an attribute carries, if the consumer reads media from it. */
export function messageShape(key: string): string | undefined {
  return SHAPES[key];
}

/** One attribute's media allowance, spent as each part is built.
 *
 * Two limits apply at once: no single payload may exceed `perItem`, and the
 * attribute as a whole may not exceed `perAttribute`. The second is what keeps
 * one span shippable on its own, since a span too large for the collector
 * cannot be split into smaller ones.
 */
export class MediaBudget {
  private remaining: number;

  constructor(
    private readonly perItem: number,
    perAttribute: number,
  ) {
    this.remaining = perAttribute;
  }

  /** A budget that admits nothing, for content read back without media. */
  static spent(): MediaBudget {
    return new MediaBudget(0, 0);
  }

  part(media: Media): MediaPart {
    // Both limits count decoded bytes, the same unit `toPart` checks; base64
    // inflates the wire by a third that the defaults allow for.
    const part = media.toPart(Math.min(this.perItem, this.remaining));
    if ('content' in part) this.remaining -= media.byteSize() ?? 0;
    return part;
  }
}

function mediaLength(value: unknown, depth = 0): number {
  if (depth > 8 || !value || typeof value !== 'object') return 0;
  if (Array.isArray(value))
    return value.reduce<number>(
      (total, item) => total + mediaLength(item, depth + 1),
      0,
    );
  const record = value as Record<string, unknown>;
  if (typeof record.type === 'string' && MEDIA_TYPES.includes(record.type)) {
    const payload = record.content ?? record.uri;
    return (typeof payload === 'string' ? payload.length : 0) + MEDIA_OVERHEAD;
  }
  return Object.values(record).reduce<number>(
    (total, item) => total + mediaLength(item, depth + 1),
    0,
  );
}

type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

// A span carries one allowance across every attribute it writes. The span is
// the unit the collector accepts or rejects, and one too large to send cannot
// be split, so counting per attribute would let three of them add up past the
// limit. Weak keys mean an ended span takes its budget with it.
const spanBudgets = new WeakMap<object, MediaBudget>();

/** The budget this span shares with every other attribute it writes.
 *
 * `owner` is any object that lives exactly as long as the span's writes do:
 * the span itself, or the attribute bag an integration fills before the span
 * exists.
 */
export function spanBudget(
  owner: object,
  policy: ContentPolicy,
  shape?: string,
): MediaBudget {
  if (!shape) return MediaBudget.spent();
  let budget = spanBudgets.get(owner);
  if (budget === undefined) {
    budget = policy.budget(shape);
    spanBudgets.set(owner, budget);
  }
  return budget;
}

export class ContentPolicy {
  readonly enabled: boolean;
  readonly maxBytes: number;
  readonly maxMediaBytes: number;
  readonly maxMediaTotalBytes: number;
  private readonly redact: ContentOptions['redact'];

  constructor(options: ContentOptions = {}) {
    this.enabled = options.captureContent ?? true;
    this.maxBytes = options.maxContentBytes ?? 16384;
    // Five MiB is the smallest per-image limit the major providers set, so
    // anything a model accepted is small enough to trace.
    this.maxMediaBytes = options.maxMediaBytes ?? 5242880;
    this.maxMediaTotalBytes = options.maxMediaTotalBytes ?? 16777216;
    this.redact = options.redact;
    if (!Number.isSafeInteger(this.maxBytes) || this.maxBytes < 64) {
      throw new Error('maxContentBytes must be an integer of at least 64');
    }
    if (!Number.isSafeInteger(this.maxMediaBytes) || this.maxMediaBytes < 0) {
      throw new Error('maxMediaBytes must be a non-negative integer');
    }
    if (
      !Number.isSafeInteger(this.maxMediaTotalBytes) ||
      this.maxMediaTotalBytes < 0
    ) {
      throw new Error('maxMediaTotalBytes must be a non-negative integer');
    }
  }

  withOptions(options: ContentOptions): ContentPolicy {
    return new ContentPolicy({
      captureContent: this.enabled,
      maxContentBytes: this.maxBytes,
      maxMediaBytes: this.maxMediaBytes,
      maxMediaTotalBytes: this.maxMediaTotalBytes,
      ...(this.redact ? { redact: this.redact } : {}),
      ...options,
    });
  }

  budget(shape?: string): MediaBudget {
    // Only a message shape is read back as media downstream; bytes anywhere
    // else are stored verbatim and help nobody.
    if (!shape) return MediaBudget.spent();
    return new MediaBudget(this.maxMediaBytes, this.maxMediaTotalBytes);
  }

  encode(
    value: unknown,
    shape?: string,
    budget: MediaBudget = this.budget(shape),
  ): string | undefined {
    if (!this.enabled) return undefined;
    try {
      const normalized = normalizeFrameworkOutput(value);
      const redacted = this.redact ? this.redact(normalized) : normalized;
      let remaining = Math.min(this.maxBytes, 1024);
      const ancestors = new Set<object>();
      const clean = (item: unknown, depth = 0): JsonValue => {
        if (--remaining < 0 || depth > 8) return '[truncated]';
        if (item === null || typeof item === 'boolean') return item;
        if (typeof item === 'number')
          return Number.isFinite(item) ? item : null;
        if (typeof item === 'string') return item.slice(0, this.maxBytes);
        if (typeof item !== 'object' || types.isProxy(item))
          return '[unsupported]';
        // After the proxy guard, since `instanceof` triggers getPrototypeOf.
        if (item instanceof Media) return budget.part(item) as JsonValue;
        if (ancestors.has(item)) return '[truncated]';
        const array = Array.isArray(item);
        const prototype = Object.getPrototypeOf(item);
        if (!array && prototype !== Object.prototype && prototype !== null)
          return '[unsupported]';
        ancestors.add(item);
        try {
          if (array) {
            const output: JsonValue[] = [];
            const length = Math.min(item.length, Math.max(0, remaining));
            for (let index = 0; index < length; index++) {
              const descriptor = Object.getOwnPropertyDescriptor(
                item,
                String(index),
              );
              output.push(
                descriptor && 'value' in descriptor
                  ? clean(descriptor.value, depth + 1)
                  : '[unsupported]',
              );
            }
            return output;
          }
          const output: Record<string, JsonValue> = Object.create(null);
          let count = Math.max(0, remaining);
          for (const key in item) {
            if (!Object.hasOwn(item, key)) continue;
            if (count-- <= 0) break;
            const descriptor = Object.getOwnPropertyDescriptor(item, key);
            output[key.slice(0, 256)] =
              descriptor && 'value' in descriptor
                ? clean(descriptor.value, depth + 1)
                : '[unsupported]';
          }
          return output;
        } finally {
          ancestors.delete(item);
        }
      };
      const cleaned = clean(redacted);
      const limit = this.maxBytes + mediaLength(cleaned);
      const encoded = JSON.stringify(cleaned).replace(
        /[\u007f-\uffff]/g,
        (character) =>
          `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
      );
      return Buffer.byteLength(encoded) > limit ? '"[truncated]"' : encoded;
    } catch {
      return undefined;
    }
  }
}
