import { describe, expect, test } from 'vitest';
import { media } from '@/integrations/extract';

const PNG = Buffer.from('89504e470d0a1a0a', 'hex');
const PNG_BASE64 = PNG.toString('base64');
const PNG_DATA_URI = `data:image/png;base64,${PNG_BASE64}`;
const PDF_DATA_URI = `data:application/pdf;base64,${PNG_BASE64}`;
const URL_PNG = 'https://example.com/photo.png';

const inline = (mimeType: string) => ({
  type: 'blob',
  mime_type: mimeType,
  content: PNG_BASE64,
});
const reference = (mimeType: string, uri: string) => ({
  type: 'uri',
  mime_type: mimeType,
  uri,
});

describe('openai', () => {
  test.each([
    ['chat image_url', { type: 'image_url', image_url: { url: PNG_DATA_URI } }],
    ['responses input_image', { type: 'input_image', image_url: PNG_DATA_URI }],
    [
      'chat file',
      { type: 'file', file: { file_data: PDF_DATA_URI, filename: 'a.pdf' } },
    ],
    ['responses input_file', { type: 'input_file', file_data: PNG_DATA_URI }],
  ])('%s carries its bytes', (_label, block) => {
    expect(media(block).toPart()).toMatchObject({
      type: 'blob',
      content: PNG_BASE64,
    });
  });

  test('a pdf file block keeps its own type', () => {
    const block = { type: 'file', file: { file_data: PDF_DATA_URI } };
    expect(media(block).toPart()).toEqual(inline('application/pdf'));
  });

  test.each([
    { type: 'image_url', image_url: { url: URL_PNG } },
    { type: 'input_image', image_url: URL_PNG },
    { type: 'input_file', file_url: URL_PNG },
  ])('a remote block travels as a reference', (block) => {
    expect(media(block).toPart()).toEqual(reference('image/png', URL_PNG));
  });

  test('audio records its type without carrying bytes', () => {
    const block = {
      type: 'input_audio',
      input_audio: { data: PNG_BASE64, format: 'wav' },
    };
    expect(media(block).toPart()).toEqual({
      type: 'blob',
      mime_type: 'audio/wav',
      content_omitted: true,
    });
  });

  test('a file referenced only by id still reports', () => {
    expect(media({ type: 'input_file', file_id: 'file-abc' }).toPart()).toEqual(
      {
        type: 'blob',
        content_omitted: true,
      },
    );
  });
});

describe('anthropic', () => {
  test.each([
    ['image', 'image/png'],
    ['document', 'application/pdf'],
  ])('a base64 %s source carries its bytes', (type, mimeType) => {
    const block = {
      type,
      source: { type: 'base64', media_type: mimeType, data: PNG_BASE64 },
    };
    expect(media(block).toPart()).toEqual(inline(mimeType));
  });

  test('a url source travels as a reference', () => {
    const block = { type: 'image', source: { type: 'url', url: URL_PNG } };
    expect(media(block).toPart()).toEqual(reference('image/png', URL_PNG));
  });

  test('a file id source still reports the media', () => {
    const block = {
      type: 'document',
      source: { type: 'file', file_id: 'file_abc' },
    };
    expect(media(block).toPart()).toEqual({
      type: 'blob',
      content_omitted: true,
    });
  });
});

describe('google', () => {
  test('inlineData carries raw bytes under its camelCase spelling', () => {
    const block = { inlineData: { mimeType: 'image/png', data: PNG } };
    expect(media(block).toPart()).toEqual(inline('image/png'));
  });

  test('inlineData accepts an already encoded payload', () => {
    const block = { inlineData: { mimeType: 'image/png', data: PNG_BASE64 } };
    expect(media(block).toPart()).toEqual(inline('image/png'));
  });

  test('fileData travels as a reference', () => {
    const block = {
      fileData: { mimeType: 'image/png', fileUri: 'gs://bucket/a.png' },
    };
    expect(media(block).toPart()).toEqual(
      reference('image/png', 'gs://bucket/a.png'),
    );
  });
});

describe('bedrock', () => {
  test('an image block resolves its format and nested bytes', () => {
    const block = { image: { format: 'png', source: { bytes: PNG } } };
    expect(media(block).toPart()).toEqual(inline('image/png'));
  });

  test('a document block resolves to a pdf', () => {
    const block = {
      document: { format: 'pdf', name: 'report', source: { bytes: PNG } },
    };
    expect(media(block).toPart()).toEqual(inline('application/pdf'));
  });

  test('a video block reports without carrying bytes', () => {
    const block = { video: { format: 'mp4', source: { bytes: PNG } } };
    expect(media(block).toPart()).toMatchObject({ content_omitted: true });
  });

  test('an s3 source travels as a reference', () => {
    const block = {
      image: {
        format: 'jpeg',
        source: { s3Location: { uri: 's3://bucket/a.jpg' } },
      },
    };
    expect(media(block).toPart()).toEqual(
      reference('image/jpeg', 's3://bucket/a.jpg'),
    );
  });
});

describe('langchain and ai sdk', () => {
  test.each([
    { type: 'image', base64: PNG_BASE64, mime_type: 'image/png' },
    {
      type: 'image',
      source_type: 'base64',
      data: PNG_BASE64,
      mime_type: 'image/png',
    },
    { type: 'file', base64: PNG_BASE64, mime_type: 'image/png' },
  ])('an inline block carries its bytes', (block) => {
    expect(media(block).toPart()).toEqual(inline('image/png'));
  });

  test.each([
    { type: 'image', url: URL_PNG },
    { type: 'image', source_type: 'url', url: URL_PNG },
  ])('a url block travels as a reference', (block) => {
    expect(media(block).toPart()).toEqual(reference('image/png', URL_PNG));
  });

  test('a block with nothing but a mime type still reports', () => {
    expect(media({ type: 'image', mime_type: 'image/png' }).toPart()).toEqual({
      type: 'blob',
      mime_type: 'image/png',
      content_omitted: true,
    });
  });
});

test('an empty block yields a part rather than throwing', () => {
  expect(media({}).toPart()).toEqual({ type: 'blob', content_omitted: true });
  expect(media(undefined).toPart()).toEqual({
    type: 'blob',
    content_omitted: true,
  });
});
