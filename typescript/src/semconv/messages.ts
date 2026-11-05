/** The currently supported language-neutral message subset, not provider SDK types. */
import type { Media } from '@/content/media';

export type GenAiPart =
  | { type: 'text'; content: string }
  | { type: 'reasoning'; content: string }
  | { type: 'tool_call'; name?: string; id?: string; arguments?: unknown }
  | { type: 'tool_call_response'; id?: string; response?: unknown }
  | Media;
export interface GenAiMessage {
  role: string;
  parts: GenAiPart[];
}
