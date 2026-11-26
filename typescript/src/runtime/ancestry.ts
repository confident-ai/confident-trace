import { diag } from '@opentelemetry/api';
import type { SpanContext } from '@opentelemetry/api';
import type { ReadableSpan } from '@opentelemetry/sdk-trace-base';

export const MAX_PENDING_SPANS = 1024;
export const MAX_PENDING_BYTES = 16 * 1024 * 1024;
export const spanKey = (ctx: SpanContext): string =>
  `${ctx.traceId}:${ctx.spanId}`;

/** Conservative payload accounting, not an RSS bound; shared values count again. */
function size(value: unknown): number {
  if (typeof value === 'string')
    return 64 + 2 * Buffer.byteLength(value, 'utf8');
  if (Array.isArray(value))
    return 64 + value.reduce((n, v) => n + 16 + size(v), 0);
  if (value && typeof value === 'object')
    return (
      128 +
      Object.entries(value).reduce((n, [k, v]) => n + 64 + size(k) + size(v), 0)
    );
  return 32;
}
function estimatedSize(span: ReadableSpan): number {
  return (
    2048 +
    size(span.name) +
    size(span.attributes) +
    size(span.events) +
    size(span.links) +
    size(span.resource.attributes) +
    size(span.instrumentationScope) +
    size(span.status)
  );
}
interface Node<R> {
  route: R;
  parent?: string | undefined;
  children: number;
  ended: boolean;
  selected: boolean;
  payload?: ReadableSpan | undefined;
  size: number;
}

/** No waits or timers; active descendants protect ancestry regardless of age. */
export class Ancestry<R extends { active: number }> {
  readonly nodes = new Map<string, Node<R>>();
  readonly pending = new Map<string, Node<R>>();
  pendingBytes = 0;
  constructor(
    private readonly publish: (route: R, span: ReadableSpan) => void,
  ) {}
  start(
    key: string,
    parent: string | undefined,
    route: R,
    selected: boolean,
  ): void {
    const ancestor = parent ? this.nodes.get(parent) : undefined;
    if (!ancestor || ancestor.route !== route) parent = undefined;
    else ancestor.children++;
    this.nodes.set(key, {
      route,
      parent,
      children: 0,
      ended: false,
      selected: false,
      size: 0,
    });
    if (selected) this.select(key);
  }
  private releasePayload(key: string, node: Node<R>): void {
    this.pending.delete(key);
    this.pendingBytes -= node.size;
    node.payload = undefined;
    node.size = 0;
  }
  private select(key: string | undefined): void {
    while (key) {
      const node = this.nodes.get(key);
      if (!node || node.selected) break;
      node.selected = true;
      if (node.payload) {
        const payload = node.payload;
        this.releasePayload(key, node);
        this.publish(node.route, payload);
      }
      key = node.parent;
    }
  }
  end(key: string, span: ReadableSpan, selected: boolean): void {
    const node = this.nodes.get(key);
    if (!node) return;
    node.ended = true;
    if (selected) this.select(key);
    if (node.selected) this.publish(node.route, span);
    else if (node.children) {
      node.payload = span;
      node.size = estimatedSize(span);
      this.pending.set(key, node);
      this.pendingBytes += node.size;
    }
    this.clean(key);
    if (
      this.pending.size > MAX_PENDING_SPANS ||
      this.pendingBytes > MAX_PENDING_BYTES
    )
      diag.debug(
        'Ancestry buffer limit reached; dropping oldest pending ancestors',
      );
    while (
      this.pending.size > MAX_PENDING_SPANS ||
      this.pendingBytes > MAX_PENDING_BYTES
    )
      this.drop(this.pending.keys().next().value!);
  }
  /**
   * Discard a buffered payload that has no AI descendant yet. The node stays
   * tracked so its subtree's bookkeeping holds; if a descendant qualifies
   * later, selection passes through it to the ancestors still held.
   */
  private drop(key: string): void {
    const node = this.nodes.get(key);
    if (node) this.releasePayload(key, node);
  }
  private clean(key: string | undefined): void {
    while (key) {
      const node = this.nodes.get(key);
      if (!node || !node.ended || node.children) break;
      this.releasePayload(key, node);
      this.nodes.delete(key);
      node.route.active--;
      key = node.parent;
      const parent = key ? this.nodes.get(key) : undefined;
      if (parent) parent.children--;
    }
  }
  clear(): void {
    for (const node of this.nodes.values()) node.route.active--;
    this.nodes.clear();
    this.pending.clear();
    this.pendingBytes = 0;
  }
}
