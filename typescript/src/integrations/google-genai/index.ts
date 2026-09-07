import { instrument } from '@/integrations/instrument';
import type { InstrumentationOptions } from '@/integrations/instrument';
export type { InstrumentationOptions } from '@/integrations/instrument';

/** Instrument content generation on this client; returns a restoration function. */
export function instrumentGoogleGenAI(
  client: {
    models: {
      generateContent: (...args: never[]) => unknown;
      generateContentStream: (...args: never[]) => unknown;
    };
  },
  options?: InstrumentationOptions,
): () => void {
  return instrument(
    [
      [client.models, 'generateContent'],
      [client.models, 'generateContentStream'],
    ],
    'google_genai',
    options,
  );
}
