import { afterEach, expect, it, vi } from 'vitest';
import { attachVercel } from '@/auto/vercel';
import { state } from '@/runtime/state';

afterEach(() => {
  vi.restoreAllMocks();
  delete state.runtime;
  state.auto.selected.clear();
  state.auto.activate.clear();
  state.auto.observed.clear();
  state.auto.warnings.clear();
});

it('reports a bridge failure once and lets the application call continue', async () => {
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
  // Only the active flag is consulted by attachment.
  state.runtime = { active: true } as NonNullable<typeof state.runtime>;
  state.auto.selected.add('vercel-ai');
  let telemetry: Record<string, unknown>;
  const sdk = {
    registerTelemetry(value: Record<string, unknown>) {
      telemetry = value;
    },
    async generateText(options?: unknown) {
      expect(options).toBeDefined();
      expect(telemetry.onStart).toBeUndefined();
      return 'still works';
    },
  };
  attachVercel(sdk, '/missing-confident-test-bridge.cjs');
  expect(warn).not.toHaveBeenCalled();
  expect(await sdk.generateText({})).toBe('still works');
  expect(await sdk.generateText({})).toBe('still works');
  expect(warn).toHaveBeenCalledTimes(1);
  expect(warn.mock.calls[0]?.[0]).toContain(
    'vercel-ai: automatic attachment failed.',
  );
  expect(warn.mock.calls[0]?.[0]).toContain('Cannot find module');
  expect(state.auto.observed.get('vercel-ai')).toBe('failed');
});
