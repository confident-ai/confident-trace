import { isDisabled } from '@/config/resolve';
import type { InitOptions } from '@/config/types';
import { state } from '@/runtime/state';
import { instrumentationNames } from '@/auto/types';
import type { InstrumentationName, InstrumentationStatus } from '@/auto/types';

export function enabled(name: InstrumentationName): boolean {
  return Boolean(
    state.runtime?.active && !isDisabled() && state.auto.selected.has(name),
  );
}
export function warn(
  key: string,
  message: string,
  integration?: InstrumentationName,
): void {
  if (state.auto.warnings.has(key) || isDisabled()) return;
  if (!state.runtime) {
    state.auto.pendingWarnings.set(key, {
      message,
      ...(integration ? { integration } : {}),
    });
    return;
  }
  if (integration && !state.auto.selected.has(integration)) return;
  state.auto.warnings.add(key);
  console.warn(`[confident-trace] ${message}`);
}
export function failed(name: InstrumentationName): void {
  state.auto.observed.set(name, 'failed');
  warn(
    name,
    `${name}: automatic attachment failed. Use the manual integration and check supported SDK versions.`,
    name,
  );
}
export function configure(options: InitOptions): void {
  const names = options.instrumentations ?? 'all';
  if (
    names !== 'all' &&
    (!Array.isArray(names) ||
      names.some((n) => !instrumentationNames.includes(n)))
  ) {
    throw new TypeError(
      'Invalid instrumentations: use "all" or an array of supported integration names',
    );
  }
  state.auto.selected = new Set(names === 'all' ? instrumentationNames : names);
  state.auto.options = options;
}
export function activate(): void {
  for (const [key, item] of state.auto.pendingWarnings)
    warn(key, item.message, item.integration);
  state.auto.pendingWarnings.clear();
  if (state.auto.selected.size && !state.auto.registered) {
    warn(
      'missing-hook',
      'Automatic tracing requires --import confident-trace/register in your Node startup command. Keep your existing entry filename. Use init({ instrumentations: [] }) for manual-only tracing.',
    );
  }
  for (const task of state.auto.activate) task();
}
export function onActivate(name: InstrumentationName, task: () => void): void {
  const guarded = () => {
    if (!enabled(name)) return;
    try {
      task();
    } catch {
      failed(name);
    }
  };
  state.auto.activate.add(guarded);
  guarded();
}
export function getInstrumentationStatus(): InstrumentationStatus {
  return {
    hookRegistered: state.auto.registered,
    integrations: Object.fromEntries(
      instrumentationNames.map((name) => [
        name,
        !state.auto.selected.has(name) || isDisabled()
          ? 'disabled'
          : (state.auto.observed.get(name) ?? 'not observed'),
      ]),
    ) as InstrumentationStatus['integrations'],
  };
}
export async function flushOwned(): Promise<void> {
  const results = await Promise.allSettled(
    [...state.auto.owned].map((item) => item.flush?.()),
  );
  if (results.some((r) => r.status === 'rejected'))
    throw new Error('Automatic tracing flush failed');
}
export async function closeOwned(): Promise<void> {
  const results = await Promise.allSettled(
    [...state.auto.owned].map((item) => item.close()),
  );
  state.auto.owned.clear();
  if (results.some((r) => r.status === 'rejected'))
    throw new Error('Automatic tracing shutdown failed');
}
