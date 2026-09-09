import { ambientOnStart } from '@/spans/index';
import { diag } from '@opentelemetry/api';
import type { Context } from '@opentelemetry/api';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import type {
  ReadableSpan,
  Span,
  SpanProcessor,
} from '@opentelemetry/sdk-trace-base';
import {
  isDisabled,
  sdkDisabled,
  resolveExportOptions,
} from '@/config/resolve';
import type { ExportOptions } from '@/config/types';
import { createHttpExporter } from '@/exporters/http';
import { createGrpcExporter } from '@/exporters/grpc';

import { context, ROOT_CONTEXT } from '@opentelemetry/api';
import type { SpanExporter } from '@opentelemetry/sdk-trace-base';
import {
  projectKey,
  setProjectRouter,
  tracingSuppressed,
  activeRecordingSpan,
} from '@/runtime/scopes';
import type { ProjectRouter, RouteScope } from '@/runtime/scopes';

class Route implements RouteScope {
  readonly identity = {};
  readonly processor: BatchSpanProcessor;
  generation = 0;
  dirty = false;
  closed = false;
  cleaning = false;
  active = 0;
  leases = 0;
  constructor(
    exporter: SpanExporter,
    readonly key?: string,
  ) {
    this.processor = new BatchSpanProcessor(exporter);
  }
  async flush(): Promise<void> {
    const generation = this.generation;
    await this.processor.forceFlush();
    if (generation === this.generation) this.dirty = false;
  }
}

class RoutingProcessor implements SpanProcessor, ProjectRouter {
  private closed = false;
  private closing: Promise<void> | undefined;
  private readonly routes = new Map<string, Route>();
  private readonly spans = new WeakMap<Span, Route>();
  private readonly retirements = new Set<Promise<void>>();
  constructor(
    private readonly fallback: Route,
    private readonly defaultKey: string | undefined,
    private readonly factory?: (apiKey: string) => SpanExporter,
  ) {}
  acquire(apiKey: string, parent: Context): Route {
    if (this.closed) throw new Error('Tracing is shut down');
    const previous =
      (parent.getValue(projectKey) as Route | undefined) ?? this.fallback;
    let route =
      apiKey === this.defaultKey ? this.fallback : this.routes.get(apiKey);
    if (activeRecordingSpan(parent) && route !== previous)
      throw new Error('Select the project before starting traced work');
    if (!route) {
      if (!this.factory)
        throw new Error(
          'Project routing requires projectExporterFactory with a custom exporter',
        );
      try {
        route = new Route(this.factory(apiKey), apiKey);
      } catch {
        throw new Error('Project exporter creation failed');
      }
      this.routes.set(apiKey, route);
    } else if (this.routes.has(apiKey)) {
      this.routes.delete(apiKey);
      this.routes.set(apiKey, route);
    }
    route.leases++;
    this.trim();
    return route;
  }
  release(scope: RouteScope): void {
    (scope as Route).leases--;
    this.trim();
  }
  private trim(): void {
    const idle = [...this.routes].filter(
      ([, r]) => !r.active && !r.leases && !r.cleaning,
    );
    for (const [key, route] of idle.slice(0, Math.max(0, idle.length - 64))) {
      route.cleaning = true;
      const retiring = route
        .flush()
        .then(async () => {
          if (route.active || route.leases || route.dirty || this.closed)
            return;
          this.routes.delete(key);
          route.closed = true;
          await route.processor.shutdown();
        })
        .catch(() => {
          diag.debug('Project exporter cleanup failed');
        })
        .finally(() => {
          route.cleaning = false;
        });
      this.retirements.add(retiring);
      void retiring.finally(() => this.retirements.delete(retiring));
    }
  }
  onStart(span: Span, parent: Context = context.active()): void {
    ambientOnStart(span, parent);
    if (this.closed || tracingSuppressed(parent)) return;
    let route =
      (parent.getValue(projectKey) as Route | undefined) ?? this.fallback;
    if (route.closed) {
      try {
        route = this.acquire(route.key!, ROOT_CONTEXT);
        route.leases--;
      } catch {
        diag.debug('Project route unavailable');
        return;
      }
    }
    route.active++;
    this.spans.set(span, route);
  }
  onEnd(span: ReadableSpan): void {
    const route = this.spans.get(span as Span);
    this.spans.delete(span as Span);
    if (!route) return;
    route.active--;
    if (this.closed) return;
    route.generation++;
    route.dirty = true;
    try {
      route.processor.onEnd(span);
    } catch {
      diag.debug('Confident Trace span processor failed');
    }
  }
  async forceFlush(): Promise<void> {
    if (this.closed) return this.closing;
    await Promise.all(
      [this.fallback, ...this.routes.values()].map((r) => r.flush()),
    );
    this.trim();
    await Promise.all(this.retirements);
  }
  shutdown(): Promise<void> {
    if (!this.closing) {
      this.closed = true;
      this.closing = Promise.all([
        ...[this.fallback, ...this.routes.values()].map((r) =>
          r.processor.shutdown(),
        ),
        ...this.retirements,
      ]).then(() => {
        this.routes.clear();
      });
    }
    return this.closing;
  }
}

/** Install on an existing provider, or let init() own the provider. */
export function createSpanProcessor(
  options: ExportOptions = {},
): SpanProcessor {
  if (sdkDisabled())
    return {
      onStart() {},
      onEnd() {},
      async forceFlush() {},
      async shutdown() {},
    };
  const baseOptions = options.exporter
    ? undefined
    : resolveExportOptions(options);
  const create = (apiKey?: string): SpanExporter => {
    const resolved = { ...baseOptions!, headers: { ...baseOptions!.headers } };
    if (apiKey !== undefined) resolved.headers['x-confident-api-key'] = apiKey;
    return resolved.protocol === 'grpc'
      ? createGrpcExporter(resolved)
      : createHttpExporter(resolved);
  };
  const exporter = options.exporter ?? create();
  const factory =
    options.projectExporterFactory ?? (options.exporter ? undefined : create);
  const processor = new RoutingProcessor(
    new Route(exporter),
    baseOptions?.headers['x-confident-api-key'] ??
      options.apiKey ??
      process.env.CONFIDENT_API_KEY,
    factory,
  );
  setProjectRouter(processor);
  return processor;
}

/** Framework exporters can supply completed spans without SDK onStart events.
 * This independent pipeline deliberately does not register a request router.
 */
export function createCompletedSpanProcessor(
  options: ExportOptions = {},
): SpanProcessor {
  const resolved = options.exporter ? undefined : resolveExportOptions(options);
  const exporter =
    options.exporter ??
    (resolved!.protocol === 'grpc'
      ? createGrpcExporter(resolved!)
      : createHttpExporter(resolved!));
  const processor = new BatchSpanProcessor(exporter);
  let closed = false;
  return {
    onStart() {},
    onEnd(span) {
      if (!closed && !isDisabled()) processor.onEnd(span);
    },
    forceFlush() {
      return closed ? Promise.resolve() : processor.forceFlush();
    },
    shutdown() {
      if (closed) return Promise.resolve();
      closed = true;
      return processor.shutdown();
    },
  };
}
