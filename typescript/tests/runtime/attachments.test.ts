import type * as PublicApi from '@/index';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { inspect } from 'node:util';
import { context, trace, propagation } from '@opentelemetry/api';
import type { Attributes } from '@opentelemetry/api';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { InitOptions } from '@/config/types';

const WAV = Buffer.concat([
  Buffer.from('RIFF$\0\0\0WAVEfmt '),
  Buffer.alloc(64),
]);
const WAV_BASE64 = WAV.toString('base64');
const PNG = Buffer.from(
  '89504e470d0a1a0a0000000d4948445200000001000000010806000000',
  'hex',
);
const MARKER = /\[CONFIDENT:(?:IMAGE|PDF):([0-9a-f]{32})\]/g;
const SPAN_INPUT = 'confident.span.input';
const SPAN_OUTPUT = 'confident.span.output';
const TRACE_INPUT = 'confident.trace.input';
const ATTACHMENTS = 'confident.span.attachments';

let api!: typeof PublicApi;
let exporter: InMemorySpanExporter;
// A runtime is initialised once per module load, so each policy gets a fresh one.
async function start(options: InitOptions = {}): Promise<void> {
  await (api as typeof PublicApi | undefined)?.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.resetModules();
  api = await import('@/index');
  exporter = new InMemorySpanExporter();
  api.init({ exporter, ...options });
}
beforeEach(async () => {
  vi.stubEnv('OTEL_SDK_DISABLED', 'false');
  await start();
});
afterEach(async () => {
  await api.shutdown();
  trace.disable();
  context.disable();
  propagation.disable();
  vi.unstubAllEnvs();
});

async function exported(): Promise<Attributes> {
  await api.flush();
  const [span] = exporter.getFinishedSpans();
  return span!.attributes;
}
function attachments(row: Attributes): Record<string, Record<string, string>> {
  return JSON.parse(String(row[ATTACHMENTS]));
}
function json(value: unknown): unknown {
  return JSON.parse(String(value));
}
function markerIds(value: unknown): string[] {
  return [...String(value).matchAll(MARKER)].map((m) => m[1]!);
}
function media(data: Buffer, mimeType?: string) {
  return api.Media.fromBytes(data, mimeType)!;
}
function image() {
  return media(PNG, 'image/png');
}

describe('images and PDFs travel as markers plus attachments', () => {
  it('writes a media value as a marker with its bytes attached', async () => {
    const picture = image();
    api.span({ name: 'describe' }, () => api.updateSpan({ output: picture }))();
    const row = await exported();
    expect(json(row[SPAN_OUTPUT])).toBe(String(picture));
    const attachment = attachments(row)[picture.id]!;
    expect(attachment.mimeType).toBe('image/png');
    expect(Buffer.from(attachment.dataBase64!, 'base64')).toEqual(PNG);
    expect(String(row[SPAN_OUTPUT])).not.toContain(attachment.dataBase64);
  });

  it('attaches media formatted into a string', async () => {
    const picture = image();
    api.span({ name: 'describe' }, () =>
      api.updateTrace({ input: `What is in this picture? ${picture}` }),
    )();
    const row = await exported();
    expect(json(row[TRACE_INPUT])).toBe(
      `What is in this picture? [CONFIDENT:IMAGE:${picture.id}]`,
    );
    expect(attachments(row)[picture.id]!.mimeType).toBe('image/png');
  });

  it('shares one attribute between trace and span fields', async () => {
    const question = image();
    const pdf = media(PNG, 'application/pdf');
    api.span({ name: 'review' }, () => {
      api.updateTrace({ input: question });
      api.updateSpan({ metadata: { doc: pdf } });
    })();
    expect(Object.keys(attachments(await exported())).sort()).toEqual(
      [question.id, pdf.id].sort(),
    );
  });

  it('drops only what a rewritten field named', async () => {
    const kept = image();
    api.span({ name: 'review' }, () => {
      api.updateSpan({ input: kept, output: image() });
      api.updateSpan({ output: 'no media now' });
    })();
    expect(Object.keys(attachments(await exported()))).toEqual([kept.id]);
  });

  it('spends the budget once for media named twice', async () => {
    await start({ maxMediaBytes: PNG.length });
    const picture = image();
    api.span({ name: 'review' }, () =>
      api.updateSpan({ input: picture, output: `replying to ${picture}` }),
    )();
    const row = await exported();
    expect(markerIds(row[SPAN_INPUT])).toEqual([picture.id]);
    expect(markerIds(row[SPAN_OUTPUT])).toEqual([picture.id]);
    expect(Object.keys(attachments(row))).toEqual([picture.id]);
  });

  it('writes media over the item limit as a note', async () => {
    await start({ maxMediaBytes: PNG.length - 1 });
    api.span({ name: 'review' }, () => api.updateSpan({ output: image() }))();
    const row = await exported();
    expect(json(row[SPAN_OUTPUT])).toBe(
      '<inline_data: image/png, not captured>',
    );
    expect(row[ATTACHMENTS]).toBeUndefined();
  });

  it('attaches a remote reference as its url', async () => {
    const picture = api.Media.fromUri('https://example.com/photo.png')!;
    api.span({ name: 'review' }, () => api.updateSpan({ output: picture }))();
    expect(attachments(await exported())[picture.id]).toEqual({
      url: 'https://example.com/photo.png',
      mimeType: 'image/png',
    });
  });

  it('reads a file when the field is written', async () => {
    const path = join(mkdtempSync(join(tmpdir(), 'ct-')), 'shot.png');
    writeFileSync(path, PNG);
    const picture = api.Media.fromFile(path)!;
    expect(picture.mimeType).toBe('image/png');
    api.span({ name: 'review' }, () => api.updateSpan({ output: picture }))();
    const attachment = attachments(await exported())[picture.id]!;
    expect(Buffer.from(attachment.dataBase64!, 'base64')).toEqual(PNG);
  });

  it.each(['video/mp4', 'text/csv', undefined])(
    'writes %s media as a note',
    async (mimeType) => {
      const value = media(WAV, mimeType);
      expect(String(value)).not.toMatch(MARKER);
      api.span({ name: 'review' }, () => api.updateSpan({ output: value }))();
      const row = await exported();
      expect(json(row[SPAN_OUTPUT])).toBe(value.note());
      expect(row[ATTACHMENTS]).toBeUndefined();
    },
  );

  it('leaves marker text naming unknown media alone', async () => {
    const text = `[CONFIDENT:IMAGE:${'0'.repeat(32)}]`;
    api.span({ name: 'review' }, () => api.updateSpan({ output: text }))();
    const row = await exported();
    expect(json(row[SPAN_OUTPUT])).toBe(text);
    expect(row[ATTACHMENTS]).toBeUndefined();
  });

  it('sends neither marker nor bytes with capture disabled', async () => {
    await start({ captureContent: false });
    api.span({ name: 'review' }, () => api.updateSpan({ output: image() }))();
    const row = await exported();
    expect(row[SPAN_OUTPUT]).toBeUndefined();
    expect(row[ATTACHMENTS]).toBeUndefined();
  });
});

describe('audio travels inline as {mimeType, dataBase64 | url}', () => {
  it('writes audio in a message beside its text', async () => {
    api.span({ name: 'turn' }, () =>
      api.updateTrace({
        input: {
          role: 'user',
          content: "What's my balance?",
          audio: media(WAV, 'audio/wav'),
        },
      }),
    )();
    const row = await exported();
    expect(json(row[TRACE_INPUT])).toEqual({
      role: 'user',
      content: "What's my balance?",
      audio: { mimeType: 'audio/wav', dataBase64: WAV_BASE64 },
    });
    expect(row[ATTACHMENTS]).toBeUndefined();
  });

  it('writes audio under any key beside other media', async () => {
    const picture = image();
    api.span({ name: 'turn' }, () =>
      api.updateSpan({
        output: [
          {
            role: 'assistant',
            content: `Here it is: ${picture}`,
            voice: media(WAV, 'audio/ogg'),
          },
        ],
      }),
    )();
    const row = await exported();
    const [message] = json(row[SPAN_OUTPUT]) as Record<string, unknown>[];
    expect(message!.voice).toEqual({
      mimeType: 'audio/ogg',
      dataBase64: WAV_BASE64,
    });
    expect(Object.keys(attachments(row))).toEqual([picture.id]);
  });

  it('writes remote audio as its url', async () => {
    api.span({ name: 'turn' }, () =>
      api.updateSpan({
        output: { audio: api.Media.fromUri('https://x.io/a.mp3') },
      }),
    )();
    expect(json((await exported())[SPAN_OUTPUT])).toEqual({
      audio: { mimeType: 'audio/mpeg', url: 'https://x.io/a.mp3' },
    });
  });

  it('reads an audio file when the field is written', async () => {
    const path = join(mkdtempSync(join(tmpdir(), 'ct-')), 'turn.wav');
    writeFileSync(path, WAV);
    api.span({ name: 'turn' }, () =>
      api.updateSpan({ input: { audio: api.Media.fromFile(path) } }),
    )();
    expect(json((await exported())[SPAN_INPUT])).toEqual({
      audio: { mimeType: 'audio/wav', dataBase64: WAV_BASE64 },
    });
  });

  it('exempts audio from the text limit', async () => {
    await start({ maxContentBytes: 64 });
    const long = Buffer.concat(Array.from({ length: 50 }, () => WAV));
    api.span({ name: 'turn' }, () =>
      api.updateSpan({
        input: { role: 'user', content: 'hi', audio: media(long, 'audio/wav') },
      }),
    )();
    const value = json((await exported())[SPAN_INPUT]) as {
      audio: { dataBase64: string };
    };
    expect(Buffer.from(value.audio.dataBase64, 'base64')).toEqual(long);
  });

  it('writes audio over the item limit as a note', async () => {
    await start({ maxMediaBytes: WAV.length - 1 });
    api.span({ name: 'turn' }, () =>
      api.updateSpan({ input: { audio: media(WAV, 'audio/wav') } }),
    )();
    expect(json((await exported())[SPAN_INPUT])).toEqual({
      audio: '<inline_data: audio/wav, not captured>',
    });
  });

  it('shares the span media total with other audio', async () => {
    await start({ maxMediaTotalBytes: WAV.length });
    api.span({ name: 'turn' }, () =>
      api.updateSpan({
        input: { audio: media(WAV, 'audio/wav') },
        output: { audio: media(WAV, 'audio/wav') },
      }),
    )();
    const row = await exported();
    expect(json(row[SPAN_INPUT])).toEqual({
      audio: { mimeType: 'audio/wav', dataBase64: WAV_BASE64 },
    });
    expect(json(row[SPAN_OUTPUT])).toEqual({
      audio: '<inline_data: audio/wav, not captured>',
    });
  });

  it('writes audio formatted into a string as a note', async () => {
    const audio = media(WAV, 'audio/wav');
    expect(String(audio)).toBe('<inline_data: audio/wav, not captured>');
    api.span({ name: 'turn' }, () =>
      api.updateSpan({ input: `Listen: ${audio}` }),
    )();
    const row = await exported();
    expect(json(row[SPAN_INPUT])).toBe(`Listen: ${audio.note()}`);
    expect(row[ATTACHMENTS]).toBeUndefined();
  });

  it('sends no audio with capture disabled', async () => {
    await start({ captureContent: false });
    api.span({ name: 'turn' }, () =>
      api.updateSpan({ input: { audio: media(WAV, 'audio/wav') } }),
    )();
    expect((await exported())[SPAN_INPUT]).toBeUndefined();
  });
});

it('keeps the payload out of inspected logs', () => {
  const audio = media(WAV, 'audio/wav');
  expect(inspect(audio)).toBe(
    'Media(mimeType=audio/wav, source=inline, bytes=?)',
  );
});
