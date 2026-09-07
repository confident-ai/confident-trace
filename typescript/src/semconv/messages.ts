/** The currently supported language-neutral message subset, not provider SDK types. */
export type GenAiPart =
  | { type: 'text'; content: string }
  | { type: 'tool_call'; name?: string; id?: string; arguments?: unknown };
export interface GenAiMessage {
  role: string;
  parts: GenAiPart[];
}
