import { types } from 'node:util';
import { frameworkOutputs } from '@/runtime/state';

/** Resolve only outputs registered by integrations, including nested graph state.
 * Preserve descriptors so normalization never executes user getters or toJSON.
 * Run before redaction so normalized content cannot bypass the user's redactor.
 */
export function normalizeFrameworkOutput(value: unknown): unknown {
  const copies = new WeakMap<object, object>();
  let remaining = 1024;
  const visit = (item: unknown, depth: number): unknown => {
    if (
      !item ||
      typeof item !== 'object' ||
      types.isProxy(item) ||
      depth > 8 ||
      --remaining < 0
    )
      return item;
    if (frameworkOutputs.has(item))
      return visit(frameworkOutputs.get(item), depth + 1);
    if (copies.has(item)) return copies.get(item);
    const prototype = Object.getPrototypeOf(item);
    if (
      !Array.isArray(item) &&
      prototype !== Object.prototype &&
      prototype !== null
    )
      return item;
    const copy = Array.isArray(item) ? [] : Object.create(prototype);
    copies.set(item, copy);
    if (Array.isArray(item)) copy.length = item.length;
    for (const key of Object.keys(item).slice(0, 1024)) {
      const descriptor = Object.getOwnPropertyDescriptor(item, key)!;
      if ('value' in descriptor)
        descriptor.value = visit(descriptor.value, depth + 1);
      Object.defineProperty(copy, key, descriptor);
    }
    return copy;
  };
  return visit(value, 0);
}
