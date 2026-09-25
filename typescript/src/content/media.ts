import { readFileSync, statSync } from 'node:fs';
import { posix } from 'node:path';
import { fileURLToPath } from 'node:url';

/** A GenAI content part carrying a payload, or recording one it could not carry. */
export type MediaPart =
  | { type: 'blob'; mime_type?: string; content: string }
  | { type: 'blob'; mime_type?: string; content_omitted: true }
  | { type: 'uri'; mime_type?: string; uri: string };

const DATA_URI = 'data:';
const FILE_URI = 'file:';
const REMOTE_SCHEMES = ['http', 'https'];

const MIME_BY_EXTENSION: Record<string, string> = {
  '.avif': 'image/avif',
  '.bmp': 'image/bmp',
  '.gif': 'image/gif',
  '.heic': 'image/heic',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.tif': 'image/tiff',
  '.tiff': 'image/tiff',
  '.webp': 'image/webp',
};

const PDF_MIME_TYPES = ['application/pdf', 'application/x-pdf'];

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

export interface MediaOptions {
  uri?: string | undefined;
  data?: Bytes | undefined;
  encoded?: string | undefined;
  mimeType?: string | undefined;
}

/** One non-text payload of a model call, read only when a part is built. */
export class Media {
  readonly mimeType: string | undefined;
  readonly uri: string | undefined;
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

  /** Read an opaque value; prefer a constructor when the field is named. */
  static parse(value: unknown, mimeType?: string): Media | undefined {
    if (isBytes(value)) return Media.fromBytes(value, mimeType);
    if (typeof value !== 'string' || !value) return undefined;
    if (value.startsWith(DATA_URI)) return Media.fromDataUri(value);
    const prefix = scheme(value);
    if (REMOTE_SCHEMES.includes(prefix) || value.startsWith(FILE_URI))
      return Media.fromUri(value, mimeType);
    // A declared mime type is what separates raw base64 from a short path.
    if (mimeType !== undefined && isBase64(value))
      return Media.fromBase64(value, mimeType);
    if (value.length <= MAX_PATH_LENGTH) return Media.fromUri(value, mimeType);
    return undefined;
  }

  get isInline(): boolean {
    return this.#data !== undefined || this.#encoded !== undefined;
  }

  get isRemote(): boolean {
    // Any scheme we cannot open ourselves is somebody else's to fetch, so
    // gs:// and s3:// travel as references rather than being dropped.
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

  /** Decoded size, without reading a file or decoding base64. */
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
    // A reference costs nothing to carry whatever its type.
    if (this.isRemote) return { type: 'uri', ...mime, uri: this.uri! };
    const encoded = supportedMedia(this.mimeType)
      ? this.base64(maxBytes)
      : undefined;
    return encoded === undefined
      ? { type: 'blob', ...mime, content_omitted: true }
      : { type: 'blob', ...mime, content: encoded };
  }

  toString(): string {
    return `Media(mimeType=${this.mimeType ?? '?'}, source=${
      this.uri ?? 'inline'
    }, bytes=${this.#size ?? '?'})`;
  }
}
