import { describe, expect, test } from 'vitest';
import { ContentPolicy } from '@/content/policy';
import { Media } from '@/content/media';
import { messages } from '@/integrations/extract';
import { frameworkMessages } from '@/integrations/framework';
import * as S from '@/semconv/generated';

const PNG = Buffer.from('89504e470d0a1a0a', 'hex');
const PNG_BASE64 = PNG.toString('base64');
const PNG_DATA_URI = `data:image/png;base64,${PNG_BASE64}`;
const BIG = Buffer.alloc(4096, 7);
const BIG_BASE64 = BIG.toString('base64');

const shape = 'input-messages';
const userMessage = (...parts: unknown[]) => [{ role: 'user', parts }];
const partsOf = (policy: ContentPolicy, value: unknown) =>
  JSON.parse(policy.encode(value, shape)!)[0].parts;

describe('budget', () => {
  test('media survives a text limit it dwarfs', () => {
    const value = userMessage(
      { type: 'text', content: 'hi' },
      Media.fromBytes(BIG, 'image/png'),
    );
    const parts = partsOf(new ContentPolicy(), value);
    expect(parts[0]).toEqual({ type: 'text', content: 'hi' });
    expect(parts[1].content).toBe(BIG_BASE64);
  });

  test('text is still bounded while media passes', () => {
    const value = userMessage(
      { type: 'text', content: 'x'.repeat(5000) },
      Media.fromBytes(PNG, 'image/png'),
    );
    const parts = partsOf(new ContentPolicy({ maxContentBytes: 256 }), value);
    expect(parts[0].content.length).toBeLessThanOrEqual(256);
    expect(parts[1].content).toBe(PNG_BASE64);
  });

  test('media over its own budget is omitted, never truncated', () => {
    const value = userMessage(Media.fromBytes(BIG, 'image/png'));
    const parts = partsOf(new ContentPolicy({ maxMediaBytes: 16 }), value);
    expect(parts[0]).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content_omitted: true,
    });
  });

  test('a zero budget turns inline media off', () => {
    const value = userMessage(Media.fromBytes(PNG, 'image/png'));
    expect(
      partsOf(new ContentPolicy({ maxMediaBytes: 0 }), value)[0],
    ).toMatchObject({ content_omitted: true });
  });

  test('a reference needs no budget', () => {
    const value = userMessage(Media.fromUri('https://example.com/a.png'));
    expect(
      partsOf(new ContentPolicy({ maxMediaBytes: 0 }), value)[0],
    ).toMatchObject({ type: 'uri', uri: 'https://example.com/a.png' });
  });

  test('media outside a message shape carries no bytes', () => {
    const encoded = new ContentPolicy().encode({
      page: Media.fromBytes(BIG, 'image/png'),
    });
    expect(JSON.parse(encoded!).page).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content_omitted: true,
    });
  });

  test('withOptions carries the media budget', () => {
    expect(new ContentPolicy().withOptions({}).maxMediaBytes).toBe(5242880);
    expect(
      new ContentPolicy({ maxMediaBytes: 32 }).withOptions({}).maxMediaBytes,
    ).toBe(32);
  });

  test('a negative budget is rejected', () => {
    expect(() => new ContentPolicy({ maxMediaBytes: -1 })).toThrow();
  });
});

describe('message shapes', () => {
  test.each([
    [S.ATTR_GEN_AI_INPUT_MESSAGES, true],
    [S.ATTR_GEN_AI_OUTPUT_MESSAGES, true],
    [S.ATTR_GEN_AI_SYSTEM_INSTRUCTIONS, true],
    [S.ATTR_CONFIDENT_TRACE_INPUT, false],
    [S.ATTR_CONFIDENT_SPAN_INPUT, false],
  ])('%s carries media: %s', async (key, carries) => {
    const { messageShape } = await import('@/content/policy');
    expect(messageShape(key) !== undefined).toBe(carries);
  });
});

describe('provider and framework extraction reach the policy', () => {
  test('provider blocks become blob parts', () => {
    const value = messages([
      {
        role: 'user',
        content: [
          { type: 'text', text: 'what is this?' },
          { type: 'image_url', image_url: { url: PNG_DATA_URI } },
        ],
      },
    ]);
    const parts = partsOf(new ContentPolicy(), value);
    expect(parts.map((p: { type: string }) => p.type)).toEqual([
      'text',
      'blob',
    ]);
    expect(parts[1]).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content: PNG_BASE64,
    });
  });

  test('ai sdk blocks become blob parts', () => {
    const value = frameworkMessages([
      {
        role: 'user',
        content: [
          { type: 'text', text: 'what is this?' },
          { type: 'image', image: PNG_DATA_URI },
        ],
      },
    ]);
    const parts = partsOf(new ContentPolicy(), value);
    expect(parts[0]).toEqual({ type: 'text', content: 'what is this?' });
    expect(parts[1]).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content: PNG_BASE64,
    });
  });

  test('an ai sdk file part keeps its declared media type', () => {
    const value = frameworkMessages([
      {
        role: 'user',
        content: [
          { type: 'file', data: PNG_BASE64, mediaType: 'application/pdf' },
        ],
      },
    ]);
    expect(partsOf(new ContentPolicy(), value)[0]).toEqual({
      type: 'blob',
      mime_type: 'application/pdf',
      content: PNG_BASE64,
    });
  });
});

describe('the per-attribute media total', () => {
  const twoImages = () =>
    userMessage(
      Media.fromBytes(PNG, 'image/png'),
      Media.fromBytes(PNG, 'image/png'),
    );

  test('later payloads stop once the span total is spent', () => {
    const policy = new ContentPolicy({ maxMediaTotalBytes: PNG.byteLength });
    const parts = partsOf(policy, twoImages());
    expect(parts[0].content).toBe(PNG_BASE64);
    expect(parts[1].content_omitted).toBe(true);
  });

  test('references do not spend the total', () => {
    const policy = new ContentPolicy({ maxMediaTotalBytes: PNG.byteLength });
    const value = userMessage(
      Media.fromUri('https://example.com/a.png'),
      Media.fromBytes(PNG, 'image/png'),
    );
    const parts = partsOf(policy, value);
    expect(parts[0].type).toBe('uri');
    expect(parts[1].content).toBe(PNG_BASE64);
  });

  test('each attribute gets its own total', () => {
    const policy = new ContentPolicy({ maxMediaTotalBytes: PNG.byteLength });
    for (let i = 0; i < 3; i++) {
      const parts = partsOf(
        policy,
        userMessage(Media.fromBytes(PNG, 'image/png')),
      );
      expect(parts[0].content).toBe(PNG_BASE64);
    }
  });

  test('the per-item limit still applies under a large total', () => {
    const policy = new ContentPolicy({
      maxMediaBytes: PNG.byteLength - 1,
      maxMediaTotalBytes: 1_000_000,
    });
    const parts = partsOf(
      policy,
      userMessage(Media.fromBytes(PNG, 'image/png')),
    );
    expect(parts[0].content_omitted).toBe(true);
  });

  test('withOptions carries the total and a negative one is rejected', () => {
    expect(new ContentPolicy().withOptions({}).maxMediaTotalBytes).toBe(
      16777216,
    );
    expect(() => new ContentPolicy({ maxMediaTotalBytes: -1 })).toThrow();
  });
});

describe('one budget per span', () => {
  const policy = new ContentPolicy({ maxMediaTotalBytes: PNG.byteLength });

  test('every attribute of a span shares the allowance', async () => {
    const { spanBudget } = await import('@/content/policy');
    const span = {};
    const first = spanBudget(span, policy, 'input-messages');
    expect(spanBudget(span, policy, 'output-messages')).toBe(first);
    expect(spanBudget(span, policy, undefined)).not.toBe(first);
  });

  test('separate spans do not share one', async () => {
    const { spanBudget } = await import('@/content/policy');
    expect(spanBudget({}, policy, shape)).not.toBe(
      spanBudget({}, policy, shape),
    );
  });

  test('a total spent on one attribute is gone from the next', async () => {
    const { spanBudget } = await import('@/content/policy');
    const span = {};
    const first = policy.encode(
      userMessage(Media.fromBytes(PNG, 'image/png')),
      shape,
      spanBudget(span, policy, shape),
    );
    const second = policy.encode(
      [
        {
          role: 'assistant',
          finish_reason: 'stop',
          parts: [Media.fromBytes(PNG, 'image/png')],
        },
      ],
      'output-messages',
      spanBudget(span, policy, 'output-messages'),
    );
    expect(JSON.parse(first!)[0].parts[0].content).toBe(PNG_BASE64);
    expect(JSON.parse(second!)[0].parts[0].content_omitted).toBe(true);
  });
});
