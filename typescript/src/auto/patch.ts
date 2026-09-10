/* SDK boundaries are structural: optional SDK types must not enter public declarations. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { context } from '@opentelemetry/api';
import type { Context } from '@opentelemetry/api';
import { state, suppress } from '@/runtime/state';
import { enabled, failed } from '@/auto/control';
import type { InstrumentationName } from '@/auto/types';

export type Foreign = any;
const created = new WeakSet<object>();
const byOriginal = new WeakMap<object, Map<string, Foreign>>();
const wrappers = new WeakMap<object, Map<string, Foreign>>();
export function replace(
  target: Foreign,
  key: string,
  make: (original: Foreign) => Foreign,
): boolean {
  if (!target || typeof target[key] !== 'function') return false;
  if (created.has(target[key])) return true;
  let keys = wrappers.get(target);
  if (!keys) {
    keys = new Map();
    wrappers.set(target, keys);
  }
  if (keys.get(key) === target[key]) return true;
  const descriptor = Object.getOwnPropertyDescriptor(target, key);
  const original = target[key];
  let shared = byOriginal.get(original);
  if (!shared) {
    shared = new Map();
    byOriginal.set(original, shared);
  }
  const wrapped = shared.get(key) ?? make(original);
  shared.set(key, wrapped);
  created.add(wrapped);
  Object.defineProperty(target, key, {
    configurable: descriptor?.configurable ?? true,
    enumerable: descriptor?.enumerable ?? false,
    writable: true,
    value: wrapped,
  });
  keys.set(key, wrapped);
  return true;
}
const constructors = new WeakMap<object, Foreign>();
export function wrapConstructor(
  original: Foreign,
  attach: (instance: Foreign) => void,
): Foreign {
  if (typeof original !== 'function') return original;
  if (constructors.has(original)) return constructors.get(original);
  const wrapped = new Proxy(original, {
    construct(target, args, newTarget) {
      const instance = Reflect.construct(target, args, newTarget);
      attach(instance);
      return instance;
    },
  });
  constructors.set(original, wrapped);
  constructors.set(wrapped, wrapped);
  return wrapped;
}
export function observed(name: InstrumentationName): void {
  // A supported SDK copy can coexist with an unsupported transitive dependency.
  if (state.auto.observed.get(name) !== 'failed')
    state.auto.observed.set(name, 'enabled');
}
export function attempt(name: InstrumentationName, task: () => void): void {
  try {
    task();
  } catch {
    failed(name);
  }
}
/** Retain scope during lazy generators as well as their initial invocation. */
export function scoped<T>(scope: Context, call: () => T): T {
  const wrap = (value: Foreign): Foreign => {
    if (value && typeof value[Symbol.asyncIterator] === 'function') {
      return new Proxy(value, {
        get(target, key) {
          if (key === Symbol.asyncIterator)
            return () => {
              const iterator = context.with(scope, () =>
                target[Symbol.asyncIterator](),
              );
              return {
                next: (...args: Foreign[]) =>
                  context.with(scope, () => iterator.next(...args)),
                return: (...args: Foreign[]) =>
                  context.with(
                    scope,
                    () =>
                      iterator.return?.(...args) ??
                      Promise.resolve({ done: true }),
                  ),
                throw: (...args: Foreign[]) =>
                  context.with(scope, () =>
                    iterator.throw
                      ? iterator.throw(...args)
                      : Promise.reject(args[0]),
                  ),
                [Symbol.asyncIterator]() {
                  return this;
                },
              };
            };
          const property = Reflect.get(target, key, target);
          return typeof property === 'function'
            ? property.bind(target)
            : property;
        },
      });
    }
    return value;
  };
  const result = context.with(scope, call);
  return (result instanceof Promise ? result.then(wrap) : wrap(result)) as T;
}
export function suppressMethods(
  target: Foreign,
  methods: string[],
  name: InstrumentationName,
): void {
  for (const method of methods)
    replace(
      target,
      method,
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          if (!enabled(name)) return original.apply(this, args);
          return scoped(context.active().setValue(suppress, true), () =>
            original.apply(this, args),
          );
        },
    );
}

export function setExport(target: Foreign, key: string, value: Foreign): void {
  if (!Reflect.set(target, key, value))
    Object.defineProperty(target, key, {
      configurable: true,
      enumerable: true,
      writable: true,
      value,
    });
}
