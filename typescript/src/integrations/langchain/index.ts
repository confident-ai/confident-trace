import { CallbackRuns } from '@/integrations/callback-runs';
import type { CallbackOptions } from '@/integrations/callback-runs';
import { frameworkMessages, safely } from '@/integrations/framework';
import { get, list } from '@/integrations/extract';
import * as S from '@/semconv/generated';

export interface LangChainCallbackOptions extends CallbackOptions {
  /** Set to agent for graph-backed agents whose callbacks otherwise look like workflows. */
  rootSpanType?: 'custom' | 'agent';
}
const roles: Record<string, string> = {
  human: 'user',
  ai: 'assistant',
  system: 'system',
  tool: 'tool',
  function: 'tool',
};
function messages(values: unknown): unknown {
  return list(values).flatMap((value) => {
    const type = get(value, 'type');
    const role =
      (typeof type === 'string' ? roles[type] : undefined) ??
      get(value, 'role') ??
      'user';
    const content = get(value, 'content');
    const normalized = frameworkMessages([{ role, content }]);
    const calls = list(get(value, 'tool_calls'));
    for (const call of calls)
      normalized[0]?.parts.push({
        type: 'tool_call',
        ...(typeof get(call, 'name') === 'string'
          ? { name: get(call, 'name') as string }
          : {}),
        ...(typeof get(call, 'id') === 'string'
          ? { id: get(call, 'id') as string }
          : {}),
        arguments: get(call, 'args'),
      });
    return normalized;
  });
}
function nameOf(
  serialized: unknown,
  name: string | undefined,
  fallback: string,
): string {
  const value =
    name ?? get(serialized, 'name') ?? list(get(serialized, 'id')).at(-1);
  return typeof value === 'string' ? value.slice(0, 1024) : fallback;
}

/** Pass one handler in the root invocation's callbacks; LangChain propagates it. */
export class ConfidentLangChainCallbackHandler {
  readonly name = 'confident-trace';
  readonly awaitHandlers = true;
  readonly raiseError = false;
  private readonly runs: CallbackRuns;
  constructor(
    private readonly options: LangChainCallbackOptions = {},
    graph = false,
  ) {
    this.runs = new CallbackRuns(
      graph ? S.INTEGRATIONS.langgraph : S.INTEGRATIONS.langchain,
      options,
    );
  }
  copy(): this {
    return this;
  }
  /** End abandoned callbacks as errors and stop accepting runs. Does not shut down OTel. */
  close(): void {
    this.runs.close();
  }
  // The 1.2.9 manager passes parentRunId fourth and runType/name seventh/eighth;
  // its BaseCallbackHandler declaration labels these string positions differently.
  handleChainStart = (
    chain: unknown,
    inputs: unknown,
    runId: string,
    parentRunId?: string,
    _tags?: string[],
    _metadata?: unknown,
    runType?: string,
    runName?: string,
  ): void => {
    safely(() => {
      const name = nameOf(chain, runName, 'chain');
      this.runs.start(
        runId,
        parentRunId,
        name,
        runType === 'agent' || name === 'AgentExecutor'
          ? 'agent'
          : parentRunId
            ? 'custom'
            : (this.options.rootSpanType ?? 'custom'),
      );
      this.runs.attribute(runId, 'langchain.run.type', runType ?? 'chain');
      this.runs.content(
        runId,
        'input',
        get(inputs, 'messages') ? messages(get(inputs, 'messages')) : inputs,
      );
    });
  };
  handleChainEnd = (output: unknown, runId: string): void =>
    this.finish(output, runId);
  handleChainError = (_error: unknown, runId: string): void =>
    this.runs.end(runId, true);
  handleToolStart = (
    tool: unknown,
    input: string,
    runId: string,
    parentRunId?: string,
    _tags?: string[],
    _metadata?: unknown,
    runName?: string,
  ): void => {
    safely(() => {
      this.runs.start(
        runId,
        parentRunId,
        nameOf(tool, runName, 'tool'),
        'tool',
      );
      this.runs.content(runId, 'input', input);
    });
  };
  handleToolEnd = (output: unknown, runId: string): void =>
    this.finish(output, runId);
  handleToolError = this.handleChainError;
  handleRetrieverStart = (
    retriever: unknown,
    query: string,
    runId: string,
    parentRunId?: string,
    _tags?: string[],
    _metadata?: unknown,
    runName?: string,
  ): void => {
    safely(() => {
      this.runs.start(
        runId,
        parentRunId,
        nameOf(retriever, runName, 'retriever'),
        'retriever',
      );
      this.runs.attribute(runId, 'langchain.run.type', 'retriever');
      this.runs.content(runId, 'input', query);
    });
  };
  handleRetrieverEnd = (output: unknown, runId: string): void =>
    this.finish(
      list(output).map((doc) => ({
        pageContent: get(doc, 'pageContent'),
        metadata: get(doc, 'metadata'),
      })),
      runId,
    );
  handleRetrieverError = this.handleChainError;
  handleLLMStart = (
    llm: unknown,
    prompts: string[],
    runId: string,
    parentRunId?: string,
    extra?: unknown,
    _tags?: string[],
    metadata?: unknown,
    runName?: string,
  ): void => {
    this.modelStart(llm, runId, parentRunId, extra, metadata, runName);
    safely(() =>
      this.runs.content(
        runId,
        'input',
        list(prompts).flatMap((p) => frameworkMessages(p)),
        true,
      ),
    );
  };
  handleChatModelStart = (
    llm: unknown,
    input: unknown,
    runId: string,
    parentRunId?: string,
    extra?: unknown,
    _tags?: string[],
    metadata?: unknown,
    runName?: string,
  ): void => {
    this.modelStart(llm, runId, parentRunId, extra, metadata, runName);
    safely(() =>
      this.runs.content(
        runId,
        'input',
        messages(list(input).flatMap(list)),
        true,
      ),
    );
  };
  handleLLMEnd = (output: unknown, runId: string): void => {
    safely(() => {
      const generations = list(get(output, 'generations')).flatMap(list);
      const messageValues = generations.map(
        (g) => get(g, 'message') ?? { type: 'ai', content: get(g, 'text') },
      );
      this.runs.content(runId, 'output', messages(messageValues), true);
      const first = messageValues[0];
      const usage =
        get(first, 'usage_metadata') ??
        get(get(output, 'llmOutput'), 'tokenUsage');
      this.runs.attribute(
        runId,
        S.ATTR_GEN_AI_USAGE_INPUT_TOKENS,
        get(usage, 'input_tokens') ?? get(usage, 'promptTokens'),
      );
      this.runs.attribute(
        runId,
        S.ATTR_GEN_AI_USAGE_OUTPUT_TOKENS,
        get(usage, 'output_tokens') ?? get(usage, 'completionTokens'),
      );
      const metadata = get(first, 'response_metadata');
      this.runs.attribute(
        runId,
        S.ATTR_GEN_AI_RESPONSE_MODEL,
        get(metadata, 'model_name') ?? get(metadata, 'model'),
      );
      this.runs.attribute(runId, S.ATTR_GEN_AI_RESPONSE_ID, get(first, 'id'));
      const reason =
        get(metadata, 'finish_reason') ??
        get(get(generations[0], 'generationInfo'), 'finish_reason');
      if (typeof reason === 'string')
        this.runs.active
          .get(runId)
          ?.span.setAttribute(S.ATTR_GEN_AI_RESPONSE_FINISH_REASONS, [reason]);
    });
    this.runs.end(runId);
  };
  handleLLMError = this.handleChainError;
  handleAgentAction = (_action: unknown, runId: string): void => {
    safely(() =>
      this.runs.attribute(runId, S.ATTR_CONFIDENT_SPAN_TYPE, 'agent'),
    );
  };
  handleAgentEnd = this.handleAgentAction;
  private modelStart(
    llm: unknown,
    id: string,
    parent: string | undefined,
    extra: unknown,
    metadata: unknown,
    name: string | undefined,
  ): void {
    safely(() => {
      this.runs.start(id, parent, nameOf(llm, name, 'chat'), 'llm');
      const params = get(extra, 'invocation_params');
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_REQUEST_MODEL,
        get(metadata, 'ls_model_name') ??
          get(params, 'model') ??
          get(params, 'model_name'),
      );
      this.runs.attribute(
        id,
        S.ATTR_GEN_AI_PROVIDER_NAME,
        get(metadata, 'ls_provider'),
      );
    });
  }
  private finish(output: unknown, id: string): void {
    safely(() =>
      this.runs.content(
        id,
        'output',
        get(output, 'messages') ? messages(get(output, 'messages')) : output,
      ),
    );
    this.runs.end(id);
  }
}
