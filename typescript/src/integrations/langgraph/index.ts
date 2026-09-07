import { ConfidentLangChainCallbackHandler } from '@/integrations/langchain';
import type { LangChainCallbackOptions } from '@/integrations/langchain';
export type LangGraphCallbackOptions = LangChainCallbackOptions;
/** Use for StateGraph and LangGraph-backed agents; children retain the LangGraph label. */
export class ConfidentLangGraphCallbackHandler extends ConfidentLangChainCallbackHandler {
  constructor(options: LangGraphCallbackOptions = {}) {
    super(options, true);
  }
}
