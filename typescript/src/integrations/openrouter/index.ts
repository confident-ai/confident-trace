import { instrument } from '@/integrations/instrument';
import type { InstrumentationOptions } from '@/integrations/instrument';
export type { InstrumentationOptions } from '@/integrations/instrument';

/** Instrument native OpenRouter chat calls in place. Returns a restoration function. */
export function instrumentOpenRouter(
  client: { chat: { send: (...args: never[]) => unknown } },
  options?: InstrumentationOptions,
): () => void {
  return instrument([[client.chat, 'send']], 'openrouter', options);
}
