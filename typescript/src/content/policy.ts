import { types } from 'node:util';
import { normalizeFrameworkOutput } from '@/content/normalize';
import {
  AUDIO_MIME_PREFIX,
  MEDIA_MARKER,
  Media,
  registered,
} from '@/content/media';
import type { MediaAttachment, MediaPart } from '@/content/media';
import type { ContentOptions } from '@/content/types';
import * as S from '@/semconv/generated';

const MEDIA_TYPES = ['blob', 'uri'];
const MEDIA_OVERHEAD = 256;
const SHAPES: Record<string, string> = {
  [S.ATTR_GEN_AI_INPUT_MESSAGES]: 'input-messages',
  [S.ATTR_GEN_AI_OUTPUT_MESSAGES]: 'output-messages',
  [S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS]: 'system-instructions',
};

export function messageShape(key: string): string | undefined {
  return SHAPES[key];
}

export class MediaBudget {
  private remaining: number;

  constructor(
    private readonly perItem: number,
    perAttribute: number,
  ) {
    this.remaining = perAttribute;
  }

  static spent(): MediaBudget {
    return new MediaBudget(0, 0);
  }

  part(media: Media): MediaPart {
    const part = media.toPart(Math.min(this.perItem, this.remaining));
    if ('content' in part) this.remaining -= media.byteSize() ?? 0;
    return part;
  }

  attachment(media: Media): MediaAttachment | undefined {
    const attachment = media.toAttachment(
      Math.min(this.perItem, this.remaining),
    );
    if (attachment && 'dataBase64' in attachment)
      this.remaining -= media.byteSize() ?? 0;
    return attachment;
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
  if (
    typeof record.mimeType === 'string' &&
    record.mimeType.startsWith(AUDIO_MIME_PREFIX)
  ) {
    const payload = record.dataBase64 ?? record.url;
    return (typeof payload === 'string' ? payload.length : 0) + MEDIA_OVERHEAD;
  }
  return Object.values(record).reduce<number>(
    (total, item) => total + mediaLength(item, depth + 1),
    0,
  );
}

type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

function audioValue(media: Media, budget: MediaBudget): JsonValue {
  const attachment = budget.attachment(media);
  return attachment === undefined
    ? media.note()
    : { mimeType: media.mimeType!, ...attachment };
}

const spanBudgets = new WeakMap<object, MediaBudget>();

/** The budget this span shares with every attribute and attachment it writes. */
export function spanMediaBudget(
  owner: object,
  policy: ContentPolicy,
): MediaBudget {
  let budget = spanBudgets.get(owner);
  if (budget === undefined) {
    budget = new MediaBudget(policy.maxMediaBytes, policy.maxMediaTotalBytes);
    spanBudgets.set(owner, budget);
  }
  return budget;
}

export function spanBudget(
  owner: object,
  policy: ContentPolicy,
  shape?: string,
): MediaBudget {
  return shape ? spanMediaBudget(owner, policy) : MediaBudget.spent();
}

export function resolveMarkers(
  encoded: string,
  budget: MediaBudget,
  carried: Readonly<Record<string, MediaAttachment>>,
): { encoded: string; found: Record<string, MediaAttachment> } {
  const found: Record<string, MediaAttachment> = {};
  const resolved = encoded.replace(MEDIA_MARKER, (marker, id: string) => {
    let attachment = found[id] ?? carried[id];
    if (attachment === undefined) {
      const media = registered(id);
      if (media === undefined) return marker;
      attachment = budget.attachment(media);
      if (attachment === undefined) return media.note();
    }
    found[id] = attachment;
    return marker;
  });
  return { encoded: resolved, found };
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
    if (!shape) return MediaBudget.spent();
    return new MediaBudget(this.maxMediaBytes, this.maxMediaTotalBytes);
  }

  encode(
    value: unknown,
    shape?: string,
    budget: MediaBudget = this.budget(shape),
    { markers = false }: { markers?: boolean } = {},
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
        if (item instanceof Media) {
          if (!markers) return budget.part(item) as JsonValue;
          return item.isAudio ? audioValue(item, budget) : String(item);
        }
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
