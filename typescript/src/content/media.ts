import { randomUUID } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { posix } from 'node:path';
import { fileURLToPath } from 'node:url';
import { inspect } from 'node:util';

/** A GenAI content part carrying a payload, or recording one it could not carry. */
export type MediaPart =
  | { type: 'blob'; mime_type?: string; content: string }
  | { type: 'blob'; mime_type?: string; content_omitted: true }
  | { type: 'uri'; mime_type?: string; uri: string };

const DATA_URI = 'data:';
const FILE_URI = 'file:';
const REMOTE_SCHEMES = ['http', 'https'];

const MIME_BY_EXTENSION: Record<string, string> = {
  '.aac': 'audio/aac',
  '.avif': 'image/avif',
  '.bmp': 'image/bmp',
  '.flac': 'audio/flac',
  '.gif': 'image/gif',
  '.heic': 'image/heic',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.m4a': 'audio/mp4',
  '.mp3': 'audio/mpeg',
  '.oga': 'audio/ogg',
  '.ogg': 'audio/ogg',
  '.opus': 'audio/opus',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.wav': 'audio/wav',
  '.webp': 'image/webp',
};

const PDF_MIME_TYPES = ['application/pdf', 'application/x-pdf'];
export const AUDIO_MIME_PREFIX = 'audio/';

export const MEDIA_MARKER = /\[CONFIDENT:(?:IMAGE|PDF):([0-9a-f]{32})\]/g;
const NOTE_MIME = /^[a-z0-9.+-]+\/[a-z0-9.+-]+$/;

const registry = new Map<string, WeakRef<Media>>();
const forget = new FinalizationRegistry<string>((id) => {
  if (registry.get(id)?.deref() === undefined) registry.delete(id);
});
const recent: Media[] = [];
const RECENT_LIMIT = 16;

export function registered(id: string): Media | undefined {
  return registry.get(id)?.deref();
}

export type MediaAttachment =
  { dataBase64: string; mimeType: string } | { url: string; mimeType?: string };

const MAX_PATH_LENGTH = 1024;
const MIN_BASE64_LENGTH = 32;
const BASE64_PATTERN = /^[A-Za-z0-9+/=\r\n]+$/;
const SCHEME_PATTERN = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

export type Bytes = Buffer | Uint8Array | ArrayBuffer;

export function isBytes(value: unknown): value is Bytes {
  return (
    value instanceof Uint8Array ||
    value instanceof ArrayBuffer ||
    Buffer.isBuffer(value)
  );
}

function scheme(uri: string): string {
  return SCHEME_PATTERN.exec(uri)?.[1]?.toLowerCase() ?? '';
}

function pathOf(uri: string): string {
  return uri.split('#')[0]!.split('?')[0]!;
}

function normalizeMime(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  return value.split(';')[0]!.trim().toLowerCase() || undefined;
}

function mimeFromPath(uri: string): string | undefined {
  return MIME_BY_EXTENSION[posix.extname(pathOf(uri)).toLowerCase()];
}

function decodedLength(encoded: string): number {
  const body = encoded.trim();
  const padding = body.length - body.replace(/=+$/, '').length;
  return Math.max(Math.floor(body.length / 4) * 3 - padding, 0);
}

function isBase64(value: string): boolean {
  return (
    value.length >= MIN_BASE64_LENGTH &&
    value.length % 4 === 0 &&
    BASE64_PATTERN.test(value)
  );
}

/** Types the consumer can keep; others cost their whole payload for nothing. */
export function supportedMedia(mimeType: string | undefined): boolean {
  return (
    typeof mimeType === 'string' &&
    (mimeType.startsWith('image/') || PDF_MIME_TYPES.includes(mimeType))
  );
}

function attachableMedia(mimeType: string | undefined): boolean {
  return (
    supportedMedia(mimeType) ||
    (typeof mimeType === 'string' && mimeType.startsWith(AUDIO_MIME_PREFIX))
  );
}

function markerType(mimeType: string | undefined): string | undefined {
  if (typeof mimeType !== 'string') return undefined;
  if (mimeType.startsWith('image/')) return 'IMAGE';
  if (PDF_MIME_TYPES.includes(mimeType)) return 'PDF';
  return undefined;
}

export interface MediaOptions {
  uri?: string | undefined;
  data?: Bytes | undefined;
  encoded?: string | undefined;
  mimeType?: string | undefined;
}

/**
 * One non-text payload, read only when a part or attachment is built.
 *
 * Formatting an image or PDF as text (`String(media)`, a template literal)
 * yields a marker that a traced field can carry anywhere in its value. Audio is
 * not formatted into text: as a value it becomes `{mimeType, dataBase64 | url}`.
 */
export class Media {
  readonly mimeType: string | undefined;
  readonly uri: string | undefined;
  readonly id = randomUUID().replaceAll('-', '');
  #data: Buffer | undefined;
  #encoded: string | undefined;
  #size: number | undefined;
  #unreadable = false;

  constructor({ uri, data, encoded, mimeType }: MediaOptions = {}) {
    this.uri = typeof uri === 'string' && uri ? uri : undefined;
    this.#data = isBytes(data) ? Buffer.from(data as Uint8Array) : undefined;
    this.#encoded =
      typeof encoded === 'string' && encoded ? encoded : undefined;
    this.mimeType =
      normalizeMime(mimeType) ??
      (this.uri === undefined ? undefined : mimeFromPath(this.uri));
  }

  static fromBytes(data: unknown, mimeType?: string): Media | undefined {
    if (!isBytes(data) || !new Uint8Array(data as Uint8Array).byteLength)
      return undefined;
    return new Media({ data: data as Bytes, mimeType });
  }

  static fromBase64(encoded: unknown, mimeType?: string): Media | undefined {
    if (typeof encoded !== 'string' || !encoded) return undefined;
    return new Media({ encoded, mimeType });
  }

  static fromUri(uri: unknown, mimeType?: string): Media | undefined {
    if (typeof uri !== 'string' || !uri) return undefined;
    return new Media({ uri, mimeType });
  }

  static fromFile(path: unknown, mimeType?: string): Media | undefined {
    if (path instanceof URL) path = path.href;
    return typeof path === 'string' ? Media.fromUri(path, mimeType) : undefined;
  }

  static fromDataUri(value: unknown): Media | undefined {
    if (typeof value !== 'string' || !value.startsWith(DATA_URI))
      return undefined;
    const separator = value.indexOf(',');
    if (separator < 0) return undefined;
    const header = value.slice(DATA_URI.length, separator);
    const payload = value.slice(separator + 1);
    if (!payload) return undefined;
    const parameters = header.split(';');
    if (!parameters.some((p) => p.trim().toLowerCase() === 'base64'))
      return undefined;
    return new Media({ encoded: payload, mimeType: parameters[0] });
  }

  static parse(value: unknown, mimeType?: string): Media | undefined {
    if (isBytes(value)) return Media.fromBytes(value, mimeType);
    if (typeof value !== 'string' || !value) return undefined;
    if (value.startsWith(DATA_URI)) return Media.fromDataUri(value);
    const prefix = scheme(value);
    if (REMOTE_SCHEMES.includes(prefix) || value.startsWith(FILE_URI))
      return Media.fromUri(value, mimeType);
    if (mimeType !== undefined && isBase64(value))
      return Media.fromBase64(value, mimeType);
    if (value.length <= MAX_PATH_LENGTH) return Media.fromUri(value, mimeType);
    return undefined;
  }

  get isInline(): boolean {
    return this.#data !== undefined || this.#encoded !== undefined;
  }

  get isAudio(): boolean {
    return this.mimeType?.startsWith(AUDIO_MIME_PREFIX) ?? false;
  }

  get isRemote(): boolean {
    if (this.isInline || this.uri === undefined) return false;
    const prefix = scheme(this.uri);
    return prefix.length > 1 && prefix !== 'file';
  }

  get filename(): string | undefined {
    if (this.uri === undefined) return undefined;
    return posix.basename(pathOf(this.uri)) || undefined;
  }

  localPath(): string | undefined {
    if (this.isInline || this.uri === undefined || this.isRemote)
      return undefined;
    const prefix = scheme(this.uri);
    if (prefix.length > 1 && prefix !== 'file') return undefined;
    if (prefix !== 'file') return this.uri;
    try {
      return fileURLToPath(this.uri) || undefined;
    } catch {
      return undefined;
    }
  }

  byteSize(): number | undefined {
    if (this.#size !== undefined) return this.#size;
    if (this.#data !== undefined) this.#size = this.#data.byteLength;
    else if (this.#encoded !== undefined)
      this.#size = decodedLength(this.#encoded);
    else {
      const path = this.localPath();
      if (path === undefined || this.#unreadable) return undefined;
      try {
        this.#size = statSync(path).size;
      } catch {
        this.#unreadable = true;
        return undefined;
      }
    }
    return this.#size;
  }

  base64(maxBytes?: number): string | undefined {
    if (maxBytes !== undefined) {
      const size = this.byteSize();
      if (size === undefined || size > maxBytes) return undefined;
    }
    if (this.#encoded !== undefined) return this.#encoded;
    if (this.#data === undefined) {
      const path = this.localPath();
      if (path === undefined || this.#unreadable) return undefined;
      try {
        this.#data = readFileSync(path);
      } catch {
        this.#unreadable = true;
        return undefined;
      }
      this.#size = this.#data.byteLength;
    }
    this.#encoded = this.#data.toString('base64');
    return this.#encoded;
  }

  toPart(maxBytes?: number): MediaPart {
    const mime =
      this.mimeType === undefined ? {} : { mime_type: this.mimeType };
    if (this.isRemote) return { type: 'uri', ...mime, uri: this.uri! };
    const encoded = supportedMedia(this.mimeType)
      ? this.base64(maxBytes)
      : undefined;
    return encoded === undefined
      ? { type: 'blob', ...mime, content_omitted: true }
      : { type: 'blob', ...mime, content: encoded };
  }

  toAttachment(maxBytes?: number): MediaAttachment | undefined {
    if (this.isRemote)
      return this.mimeType === undefined
        ? { url: this.uri! }
        : { url: this.uri!, mimeType: this.mimeType };
    if (!attachableMedia(this.mimeType)) return undefined;
    const encoded = this.base64(maxBytes);
    return encoded === undefined
      ? undefined
      : { dataBase64: encoded, mimeType: this.mimeType! };
  }

  note(): string {
    const mime =
      this.mimeType !== undefined && NOTE_MIME.test(this.mimeType)
        ? this.mimeType
        : 'unknown';
    return `<inline_data: ${mime}, not captured>`;
  }

  toString(): string {
    const kind = markerType(this.mimeType);
    if (kind === undefined) return this.note();
    if (!registry.has(this.id)) {
      registry.set(this.id, new WeakRef(this));
      forget.register(this, this.id);
    }
    recent.push(this);
    if (recent.length > RECENT_LIMIT) recent.shift();
    return `[CONFIDENT:${kind}:${this.id}]`;
  }

  // Logs show the shape, never the payload.
  [inspect.custom](): string {
    return `Media(mimeType=${this.mimeType ?? '?'}, source=${
      this.uri ?? 'inline'
    }, bytes=${this.#size ?? '?'})`;
  }
}
