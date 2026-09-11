import { readFileSync } from 'node:fs';
import { expect, it, vi } from 'vitest';
import { frameworkOutputs } from '@/runtime/state';
import { ContentPolicy } from '@/content/policy';
import type { ContentOptions } from '@/content/types';

interface Case {
  name: string;
  value: unknown;
  enabled?: boolean;
  maxBytes?: number;
  redactor?: string;
  expected?: unknown;
  expectedOmitted?: boolean;
}
const vectors = JSON.parse(
  readFileSync(
    new URL('../../../spec/content-vectors.json', import.meta.url),
    'utf8',
  ),
) as { cases: Case[] };
for (const example of vectors.cases) {
  it(`shared content: ${example.name}`, () => {
    const options: ContentOptions = {};
    if (example.enabled !== undefined) options.captureContent = example.enabled;
    if (example.maxBytes !== undefined)
      options.maxContentBytes = example.maxBytes;
    if (example.redactor)
      options.redact = () => {
        if (example.redactor === 'throw') throw new Error('secret');
        return '[redacted]';
      };
    const encoded = new ContentPolicy(options).encode(example.value);
    if (example.expectedOmitted) expect(encoded).toBeUndefined();
    else expect(JSON.parse(encoded!)).toEqual(example.expected);
  });
}
it('never invokes accessors, toJSON, iterators or proxy traps', () => {
  const touched = vi.fn(() => {
    throw new Error('must not execute');
  });
  const value = {
    get secret() {
      return touched();
    },
    toJSON: touched,
    [Symbol.iterator]: touched,
  };
  expect(JSON.parse(new ContentPolicy().encode(value)!)).toEqual({
    secret: '[unsupported]',
    toJSON: '[unsupported]',
  });
  expect(
    new ContentPolicy().encode(new Proxy({}, { getPrototypeOf: touched })),
  ).toBe('"[unsupported]"');
  expect(touched).not.toHaveBeenCalled();
});
it('handles cycles, class instances, bigint, nonfinite numbers, and prototype keys', () => {
  const cycle: Record<string, unknown> = {};
  cycle.self = cycle;
  expect(new ContentPolicy().encode(cycle)).toBe('{"self":"[truncated]"}');
  expect(new ContentPolicy().encode(new Date())).toBe('"[unsupported]"');
  expect(new ContentPolicy().encode([1n, NaN, Infinity, undefined])).toBe(
    '["[unsupported]",null,null,"[unsupported]"]',
  );
  expect(new ContentPolicy().encode(JSON.parse('{"__proto__":"safe"}'))).toBe(
    '{"__proto__":"safe"}',
  );
});
it('bounds depth, nodes, Unicode bytes, and omits content before redaction when disabled', () => {
  const redact = vi.fn();
  expect(
    new ContentPolicy({ captureContent: false, redact }).encode('secret'),
  ).toBeUndefined();
  expect(redact).not.toHaveBeenCalled();
  for (const value of [
    '😀'.repeat(100),
    Array(10000).fill('x'),
    'x'.repeat(10000),
  ]) {
    const encoded = new ContentPolicy({ maxContentBytes: 64 }).encode(value)!;
    expect(Buffer.byteLength(encoded)).toBeLessThanOrEqual(64);
    expect(() => JSON.parse(encoded)).not.toThrow();
  }
  expect(() => new ContentPolicy({ maxContentBytes: 63 })).toThrow();
});

it('applies redaction, size limits and opt-out to normalized framework output', () => {
  const result = new (class Result {})();
  frameworkOutputs.set(result, 'private answer');
  const redact = vi.fn(() => 'redacted');
  expect(new ContentPolicy({ redact }).encode(result)).toBe('"redacted"');
  expect(redact).toHaveBeenCalledWith('private answer');
  redact.mockClear();
  expect(
    new ContentPolicy({ captureContent: false, redact }).encode(result),
  ).toBeUndefined();
  expect(redact).not.toHaveBeenCalled();
  frameworkOutputs.set(result, 'x'.repeat(1000));
  expect(new ContentPolicy({ maxContentBytes: 64 }).encode(result)).toBe(
    '"[truncated]"',
  );
});

it('normalizes nested framework results before redaction without invoking getters', () => {
  const message = new (class Message {})();
  frameworkOutputs.set(message, 'secret');
  const getter = vi.fn(() => {
    throw new Error('must not run');
  });
  const input = {
    messages: [message],
    get other() {
      return getter();
    },
  };
  const redact = vi.fn((value: unknown) => {
    const data = value as { messages: string[] };
    expect(data.messages).toEqual(['secret']);
    return { messages: ['redacted'] };
  });
  expect(new ContentPolicy({ redact }).encode(input)).toBe(
    '{"messages":["redacted"]}',
  );
  expect(getter).not.toHaveBeenCalled();
  expect(input.messages[0]).toBe(message);
});
