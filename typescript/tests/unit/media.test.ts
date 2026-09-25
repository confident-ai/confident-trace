import { describe, expect, test } from 'vitest';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Media, supportedMedia } from '@/content/media';

const PNG = Buffer.from('89504e470d0a1a0a', 'hex');
const PNG_BASE64 = PNG.toString('base64');
const URL_PNG = 'https://example.com/photo.png';

function pngFile(name = 'shot.png'): string {
  const path = join(mkdtempSync(join(tmpdir(), 'ct-media-')), name);
  writeFileSync(path, PNG);
  return path;
}

describe('inline payloads', () => {
  test('bytes become a blob part', () => {
    const media = Media.fromBytes(PNG, 'image/png')!;
    expect(media.toPart()).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content: PNG_BASE64,
    });
    expect(media.byteSize()).toBe(PNG.byteLength);
  });

  test('a base64 payload is passed through without a round trip', () => {
    const media = Media.fromBase64(PNG_BASE64, 'image/png')!;
    expect(media.toPart()).toMatchObject({ content: PNG_BASE64 });
    expect(media.byteSize()).toBe(PNG.byteLength);
  });

  test('a data uri splits into payload and mime type', () => {
    const media = Media.fromDataUri(`data:image/png;base64,${PNG_BASE64}`)!;
    expect(media.mimeType).toBe('image/png');
    expect(media.toPart()).toMatchObject({ content: PNG_BASE64 });
  });

  test.each([
    'data:image/png,not-base64-encoded',
    'data:image/png;base64',
    'data:image/png;base64,',
    URL_PNG,
    '',
  ])('only base64 data uris parse as one: %s', (value) => {
    expect(Media.fromDataUri(value)).toBeUndefined();
  });
});

describe('references', () => {
  test('a remote uri travels as a reference', () => {
    const media = Media.fromUri('https://example.com/photo.jpeg?v=2')!;
    expect(media.isRemote).toBe(true);
    expect(media.toPart()).toEqual({
      type: 'uri',
      mime_type: 'image/jpeg',
      uri: 'https://example.com/photo.jpeg?v=2',
    });
  });

  test.each(['gs://bucket/a.png', 's3://bucket/a.png'])(
    'a scheme we cannot open is somebody else to fetch: %s',
    (uri) => {
      expect(Media.fromUri(uri)!.toPart()).toMatchObject({
        type: 'uri',
        uri,
      });
    },
  );

  test('a reference ignores the budget', () => {
    expect(Media.fromUri(URL_PNG)!.toPart(1)).toMatchObject({ type: 'uri' });
  });

  test('a declared mime type wins over the extension', () => {
    expect(Media.fromUri(URL_PNG, 'image/webp')!.mimeType).toBe('image/webp');
  });

  test('an unknown extension leaves the mime type unset', () => {
    expect(Media.fromUri('https://example.com/report')!.toPart()).toEqual({
      type: 'uri',
      uri: 'https://example.com/report',
    });
  });
});

describe('local files', () => {
  test('a file is read only when a part is built', () => {
    const media = Media.fromUri(pngFile())!;
    expect(media.byteSize()).toBe(PNG.byteLength); // stat, not a read
    expect(media.toPart()).toMatchObject({ content: PNG_BASE64 });
  });

  test('a file uri resolves to its path', () => {
    const path = pngFile();
    const media = Media.fromUri(pathToFileURL(path).href)!;
    expect(media.localPath()).toBe(path);
    expect(media.toPart()).toMatchObject({ content: PNG_BASE64 });
  });

  test('a missing file records the media without its content', () => {
    const media = Media.fromUri('/nowhere/gone.png')!;
    expect(media.toPart()).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content_omitted: true,
    });
    expect(media.byteSize()).toBeUndefined();
  });

  test('oversized media is omitted rather than truncated', () => {
    const media = Media.fromUri(pngFile())!;
    expect(media.toPart(PNG.byteLength - 1)).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content_omitted: true,
    });
    expect(media.toPart(PNG.byteLength)).toMatchObject({
      content: PNG_BASE64,
    });
  });
});

describe('supported types', () => {
  test.each(['audio/wav', 'video/mp4', 'text/csv', 'application/octet-stream'])(
    'bytes the consumer cannot keep are never carried: %s',
    (mimeType) => {
      expect(Media.fromBytes(PNG, mimeType)!.toPart()).toEqual({
        type: 'blob',
        mime_type: mimeType,
        content_omitted: true,
      });
      expect(supportedMedia(mimeType)).toBe(false);
    },
  );

  test.each([
    'image/png',
    'image/svg+xml',
    'application/pdf',
    'application/x-pdf',
  ])('supported types still carry their bytes: %s', (mimeType) => {
    expect(Media.fromBytes(PNG, mimeType)!.toPart()).toMatchObject({
      content: PNG_BASE64,
    });
  });

  test('a mime type is normalized', () => {
    expect(Media.fromBytes(PNG, ' IMAGE/PNG; charset=binary ')!.mimeType).toBe(
      'image/png',
    );
  });
});

describe('parse', () => {
  test.each([
    [PNG, 'image/png', { type: 'blob', content: PNG_BASE64 }],
    [new Uint8Array(PNG), 'image/png', { type: 'blob', content: PNG_BASE64 }],
    [
      `data:image/png;base64,${PNG_BASE64}`,
      undefined,
      { type: 'blob', content: PNG_BASE64 },
    ],
    [PNG_BASE64.repeat(4), 'image/png', { type: 'blob' }],
    [URL_PNG, undefined, { type: 'uri', uri: URL_PNG }],
  ])('reads the shapes providers send: %s', (value, mimeType, expected) => {
    expect(
      Media.parse(value, mimeType as string | undefined)!.toPart(),
    ).toMatchObject(expected);
  });

  test.each([undefined, null, '', 7, { url: 'a.png' }, []])(
    'declines what it cannot place: %s',
    (value) => {
      expect(Media.parse(value)).toBeUndefined();
    },
  );

  test('an undeclared string is treated as a path', () => {
    const media = Media.parse(PNG_BASE64)!;
    expect(media.uri).toBe(PNG_BASE64);
    expect(media.toPart()).toMatchObject({ content_omitted: true });
  });
});

test('string conversion never carries the payload', () => {
  const text = String(Media.fromBase64(PNG_BASE64, 'image/png'));
  expect(text).not.toContain(PNG_BASE64);
  expect(text).toContain('image/png');
});
