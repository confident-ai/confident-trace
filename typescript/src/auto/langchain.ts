import { context, createContextKey } from '@opentelemetry/api';
import { ConfidentLangChainCallbackHandler } from '@/integrations/langchain';
import { ConfidentLangGraphCallbackHandler } from '@/integrations/langgraph';
import { enabled } from '@/auto/control';
import { state } from '@/runtime/state';
import { observed, replace, scoped, suppressMethods } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

const graphScope = createContextKey('confident-trace.langgraph');
let chainHandler: ConfidentLangChainCallbackHandler | undefined;
let graphHandler: ConfidentLangGraphCallbackHandler | undefined;
function handler(graph: boolean) {
  if (graph && graphHandler) return graphHandler;
  if (!graph && chainHandler) return chainHandler;
  const result = graph
    ? new ConfidentLangGraphCallbackHandler()
    : new ConfidentLangChainCallbackHandler();
  state.auto.owned.add({ close: () => result.close() });
  if (graph) graphHandler = result;
  else chainHandler = result;
  return result;
}
export function attachLangChain(exports: Foreign): void {
  if (exports.CallbackManager) {
    replace(
      exports.CallbackManager,
      '_configureSync',
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          const graph = Boolean(context.active().getValue(graphScope));
          if (!enabled(graph ? 'langgraph' : 'langchain'))
            return original.apply(this, args);
          let manager = original.apply(this, args);
          if (
            manager?.handlers?.some(
              (h: Foreign) => h.name === 'confident-trace',
            )
          )
            return manager;
          if (!manager) manager = new exports.CallbackManager();
          else manager = manager.copy();
          manager.addHandler(handler(graph), true);
          return manager;
        },
    );
    observed('langchain');
  }
  for (const name of ['BaseChatModel', 'BaseLLM'])
    if (exports[name]) {
      // These enclose model execution, not arbitrary chain/tool execution.
      for (const method of ['generate', 'stream'])
        replace(
          exports[name].prototype,
          method,
          (original) =>
            function (this: Foreign, ...args: Foreign[]) {
              const graph = Boolean(context.active().getValue(graphScope));
              if (!enabled(graph ? 'langgraph' : 'langchain'))
                return original.apply(this, args);
              const target = { call: original };
              suppressMethods(
                target,
                ['call'],
                graph ? 'langgraph' : 'langchain',
              );
              return target.call.apply(this, args);
            },
        );
    }
}
export function attachLangGraph(exports: Foreign): void {
  if (!exports.Pregel) return;
  for (const method of ['invoke', 'stream'])
    replace(
      exports.Pregel.prototype,
      method,
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          if (!enabled('langgraph')) return original.apply(this, args);
          return scoped(context.active().setValue(graphScope, true), () =>
            original.apply(this, args),
          );
        },
    );
  observed('langgraph');
}
