export const instrumentationNames = [
  'openai',
  'anthropic',
  'google-genai',
  'vercel-ai',
  'langchain',
  'langgraph',
  'mastra',
  'openai-agents',
] as const;
export type InstrumentationName = (typeof instrumentationNames)[number];
export type InstrumentationState =
  'enabled' | 'disabled' | 'not observed' | 'unsupported' | 'failed';
export interface InstrumentationStatus {
  readonly hookRegistered: boolean;
  readonly integrations: Readonly<
    Record<InstrumentationName, InstrumentationState>
  >;
}
