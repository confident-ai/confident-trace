import { instrument } from '@/integrations/instrument';
import type { InstrumentationOptions } from '@/integrations/instrument';
export type { InstrumentationOptions } from '@/integrations/instrument';

/** Instrument this client in place. Returns an idempotent restoration function. */
export function instrumentPortkey(
  client: {
    chat: { completions: { create: (...args: never[]) => unknown } };
    responses: { create: (...args: never[]) => unknown };
  },
  options?: InstrumentationOptions,
): () => void {
  return instrument(
    [
      [client.chat.completions, 'create'],
      [client.responses, 'create'],
    ],
    'portkey',
    options,
  );
}
