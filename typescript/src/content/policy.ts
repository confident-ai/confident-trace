import { types } from 'node:util';
import type { ContentOptions } from '@/content/types';

type JsonValue =
  null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export class ContentPolicy {
  readonly enabled: boolean;
  readonly maxBytes: number;
  private readonly redact: ContentOptions['redact'];

  constructor(options: ContentOptions = {}) {
    this.enabled = options.captureContent ?? true;
    this.maxBytes = options.maxContentBytes ?? 16384;
    this.redact = options.redact;
    if (!Number.isSafeInteger(this.maxBytes) || this.maxBytes < 64) {
      throw new Error('maxContentBytes must be an integer of at least 64');
    }
  }

  withOptions(options: ContentOptions): ContentPolicy {
    return new ContentPolicy({
      captureContent: this.enabled,
      maxContentBytes: this.maxBytes,
      ...(this.redact ? { redact: this.redact } : {}),
      ...options,
    });
  }

  encode(value: unknown): string | undefined {
    if (!this.enabled) return undefined;
    try {
      const redacted = this.redact ? this.redact(value) : value;
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
      // Match Python's ASCII JSON byte accounting, including surrogate pairs.
      const encoded = JSON.stringify(clean(redacted)).replace(
        /[\u007f-\uffff]/g,
        (character) =>
          `\\u${character.charCodeAt(0).toString(16).padStart(4, '0')}`,
      );
      return Buffer.byteLength(encoded) > this.maxBytes
        ? '"[truncated]"'
        : encoded;
    } catch {
      return undefined;
    }
  }
}
