export interface ContentOptions {
  captureContent?: boolean;
  /** Maximum encoded bytes per content attribute; minimum 64, default 16384. */
  maxContentBytes?: number;
  /** Synchronous redaction. Throwing omits the content entirely. */
  redact?: (value: unknown) => unknown;
}
