import { context, trace, SpanKind, SpanStatusCode } from '@opentelemetry/api';
import type { Tracer } from '@opentelemetry/api';
import { isDisabled } from '@/config/resolve';
import { ContentPolicy } from '@/content/policy';
import type { ContentOptions } from '@/content/types';
import { state, suppress, patched, automaticOriginals } from '@/runtime/state';
import { VERSION } from '@/runtime/version';
import { Capture, get } from '@/integrations/extract';
import type { Provider } from '@/integrations/extract';
import * as S from '@/semconv/generated';

export interface InstrumentationOptions extends ContentOptions {
  /** Optional application-owned tracer; otherwise uses the global OTel provider. */
  tracer?: Tracer;
}
type Method = (...args: unknown[]) => unknown;
function safe(fn: () => void) {
  try {
    fn();
  } catch {
    /* Instrumentation must not change SDK behavior. */
  }
}

export function instrument(
  targets: [object, string][],
  provider: Provider,
  options: InstrumentationOptions = {},
): () => void {
  // Validate explicit content options at setup, before modifying any client.
  const explicit = Object.keys(options).some((k) => k !== 'tracer')
    ? new ContentPolicy(options)
    : undefined;
  const undo: (() => void)[] = [];
  try {
    for (const [target, key] of targets) {
      const current = Reflect.get(target, key) as Method;
      const automatic = automaticOriginals.get(current);
      const name = provider === 'google_genai' ? 'google-genai' : provider;
      const original = (
        automatic && !state.auto.selected.has(name) ? automatic : current
      ) as Method;
      if (typeof original !== 'function' || patched.has(original)) continue;
      const descriptor = Object.getOwnPropertyDescriptor(target, key);
      const wrapped: Method = function (this: unknown, ...args) {
        if (
          isDisabled() ||
          context.active().getValue(suppress) ||
          (state.runtime && !state.runtime.active && !options.tracer)
        )
          return original.apply(this, args);
        const policy = explicit
          ? (state.policy?.withOptions(options) ?? explicit)
          : (state.policy ?? new ContentPolicy());
        const tracer =
          options.tracer ??
          trace.getTracerProvider().getTracer('confident-trace', VERSION, {
            schemaUrl: S.SCHEMA_URL,
          });
        const parent = context.active();
        const operation =
          provider === 'google_genai' ? 'generate_content' : 'chat';
        const model = get(args[0], 'model');
        const span = tracer.startSpan(
          `${operation} ${typeof model === 'string' ? model : 'unknown'}`,
          {
            kind: SpanKind.CLIENT,
            attributes: {
              [S.ATTR_CONFIDENT_SPAN_INTEGRATION]: S.INTEGRATIONS[provider],
              [S.ATTR_CONFIDENT_SPAN_TYPE]: 'llm',
              [S.ATTR_GEN_AI_PROVIDER_NAME]:
                provider === 'google_genai' ? 'gcp.gen_ai' : provider,
              [S.ATTR_GEN_AI_OPERATION_NAME]: operation,
            },
          },
          parent,
        );
        if (!span.isRecording()) {
          span.end();
          return original.apply(this, args);
        }
        const active = trace.setSpan(parent, span).setValue(suppress, true);
        const capture = new Capture(
          span,
          policy,
          provider,
          !trace.getSpanContext(parent),
        );
        let ended = false;
        const cleanups: (() => void)[] = [];
        const end = (error?: unknown) => {
          if (ended) return;
          ended = true;
          safe(() => {
            if (error !== undefined) {
              // Error messages can contain request bodies and secrets.
              span.setStatus({ code: SpanStatusCode.ERROR });
              span.setAttribute(S.ATTR_ERROR_TYPE, 'provider_error');
            }
          });
          for (const cleanup of cleanups) safe(cleanup);
          safe(() => span.end());
        };
        const observe = (fn: () => void) => {
          if (!ended && span.isRecording()) safe(fn);
        };
        observe(() => capture.request(args[0]));
        const failed = (error: unknown): never => {
          end(error);
          throw error;
        };
        const seen = new WeakSet<object>();
        const finish = (value: unknown): unknown => {
          if (value && typeof value === 'object' && seen.has(value))
            return value;
          if (value && typeof value === 'object') {
            seen.add(value);
            const iterable = value as AsyncIterable<unknown>;
            if (typeof iterable[Symbol.asyncIterator] === 'function') {
              const originalIterator = iterable[Symbol.asyncIterator];
              try {
                Object.defineProperty(iterable, Symbol.asyncIterator, {
                  configurable: true,
                  value() {
                    const iterator = context.with(active, () =>
                      originalIterator.call(iterable),
                    );
                    const step = async (
                      method: 'next' | 'return' | 'throw',
                      arg?: unknown,
                    ): Promise<IteratorResult<unknown>> => {
                      try {
                        const fn = iterator[method];
                        if (!fn) {
                          if (method === 'throw') throw arg;
                          return { done: true, value: arg };
                        }
                        const result = await context.with(active, () =>
                          fn.call(iterator, arg),
                        );
                        if (result.done) end();
                        else observe(() => capture.chunk(result.value));
                        return result;
                      } catch (error) {
                        return failed(error);
                      } finally {
                        if (method === 'return' || method === 'throw') end();
                      }
                    };
                    return {
                      next: (arg?: unknown) => step('next', arg),
                      return: (arg?: unknown) => step('return', arg),
                      throw: (arg?: unknown) => step('throw', arg),
                      [Symbol.asyncIterator]() {
                        return this;
                      },
                    };
                  },
                });
                const controller = Reflect.get(value, 'controller') as
                  AbortController | undefined;
                if (controller?.signal) {
                  const abort = () => end(new Error('aborted'));
                  if (controller.signal.aborted) abort();
                  else {
                    controller.signal.addEventListener('abort', abort, {
                      once: true,
                    });
                    cleanups.push(() =>
                      controller.signal.removeEventListener('abort', abort),
                    );
                  }
                }
              } catch {
                end();
              }
              return value;
            }
          }
          observe(() => capture.response(value));
          end();
          return value;
        };
        try {
          const result = context.with(active, () => original.apply(this, args));
          // Anthropic's eager MessageStream supports callbacks/finalMessage without iteration.
          if (
            key === 'stream' &&
            result &&
            typeof result === 'object' &&
            typeof Reflect.get(result, 'on') === 'function'
          ) {
            const on = Reflect.get(result, 'on') as Method;
            on.call(result, 'streamEvent', (event: unknown) =>
              observe(() => capture.chunk(event)),
            );
            on.call(result, 'error', (error: unknown) => end(error));
            on.call(result, 'abort', (error: unknown) => end(error));
            on.call(result, 'end', () => end());
            return result;
          }
          if (
            result &&
            typeof result === 'object' &&
            typeof Reflect.get(result, 'then') === 'function'
          ) {
            let parsed: Promise<unknown> | undefined;
            const parse = () =>
              (parsed ??= Promise.resolve(result).then(finish, failed));
            return new Proxy(result, {
              get(target, property) {
                if (
                  property === 'then' ||
                  property === 'catch' ||
                  property === 'finally'
                )
                  return (...a: unknown[]) =>
                    Reflect.apply(Reflect.get(parse(), property), parse(), a);
                const member = Reflect.get(target, property, target);
                if (property === 'withResponse' && typeof member === 'function')
                  return (...a: unknown[]) =>
                    Promise.resolve(Reflect.apply(member, target, a)).then(
                      (envelope) => {
                        const data = finish(get(envelope, 'data'));
                        return { ...(envelope as object), data };
                      },
                      failed,
                    );
                if (property === 'asResponse' && typeof member === 'function')
                  return (...a: unknown[]) =>
                    Promise.resolve(Reflect.apply(member, target, a)).then(
                      (response) => {
                        end();
                        return response;
                      },
                      failed,
                    );
                return typeof member === 'function'
                  ? member.bind(target)
                  : member;
              },
            });
          }
          return finish(result);
        } catch (error) {
          return failed(error);
        }
      };
      patched.add(wrapped);
      Object.defineProperty(target, key, {
        configurable: true,
        writable: true,
        enumerable: descriptor?.enumerable ?? false,
        value: wrapped,
      });
      undo.push(() => {
        if (Reflect.get(target, key) !== wrapped) return;
        if (descriptor) Object.defineProperty(target, key, descriptor);
        else Reflect.deleteProperty(target, key);
      });
    }
  } catch (error) {
    for (const restore of undo.reverse()) restore();
    throw error;
  }
  return () => {
    for (const restore of undo.splice(0).reverse()) restore();
  };
}
