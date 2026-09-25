export interface ContentOptions {
  captureContent?: boolean;
  /** Maximum encoded bytes per content attribute; minimum 64, default 16384. */
  maxContentBytes?: number;
  /** Maximum decoded bytes per media payload; 0 disables inline media. */
  maxMediaBytes?: number;
  /** Maximum decoded media bytes per content attribute. */
  maxMediaTotalBytes?: number;
  /** Synchronous redaction. Throwing omits the content entirely. */
  redact?: (value: unknown) => unknown;
}
