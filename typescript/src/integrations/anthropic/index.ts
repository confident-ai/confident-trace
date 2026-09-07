import { instrument } from '@/integrations/instrument';
import type { InstrumentationOptions } from '@/integrations/instrument';
export type { InstrumentationOptions } from '@/integrations/instrument';

/** Instrument Messages create/stream on this client; returns a restoration function. */
export function instrumentAnthropic(
  client: {
    messages: {
      create: (...args: never[]) => unknown;
      stream: (...args: never[]) => unknown;
    };
  },
  options?: InstrumentationOptions,
): () => void {
  return instrument(
    [
      [client.messages, 'create'],
      [client.messages, 'stream'],
    ],
    'anthropic',
    options,
  );
}
