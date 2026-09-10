import { afterEach, expect, it } from 'vitest';
import { observed } from '@/auto/patch';
import { state } from '@/runtime/state';

afterEach(() => state.auto.observed.clear());
it('reports a supported attachment even when another SDK copy is unsupported', () => {
  state.auto.observed.set('openai', 'unsupported');
  observed('openai');
  expect(state.auto.observed.get('openai')).toBe('enabled');
});
it('retains attachment failures', () => {
  state.auto.observed.set('openai', 'failed');
  observed('openai');
  expect(state.auto.observed.get('openai')).toBe('failed');
});
