import { ConfidentOpenAIAgentsProcessor } from '@/integrations/openai-agents';
import { enabled, onActivate } from '@/auto/control';
import { state, frameworkOutputs } from '@/runtime/state';
import { observed, replace, suppressMethods } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

const installed = new WeakSet<object>();
const marker = Symbol.for('confident-trace.agents.processor');
let automatic: Foreign;
let manualAttached = false;
function isManual(value: Foreign): boolean {
  return (
    !value?.[marker] &&
    value?.constructor?.name === 'ConfidentOpenAIAgentsProcessor'
  );
}
function processor(): Foreign {
  if (automatic) return automatic;
  const adapter = new ConfidentOpenAIAgentsProcessor({ flush: async () => {} });
  const delivered = new Map<PropertyKey, WeakSet<object>>();
  automatic = new Proxy(adapter, {
    get(target, key) {
      if (key === marker) return true;
      // Runtime owns this adapter; SDK processor replacement must not close it.
      if (key === 'shutdown') return async () => {};
      const method = Reflect.get(target, key, target);
      if (typeof method !== 'function') return method;
      return (...args: Foreign[]) => {
        if (String(key).startsWith('on')) {
          if (!enabled('openai-agents') || manualAttached)
            return Promise.resolve();
          let seen = delivered.get(key);
          if (!seen) {
            seen = new WeakSet();
            delivered.set(key, seen);
          }
          if (seen.has(args[0])) return Promise.resolve();
          seen.add(args[0]);
        }
        return method.apply(target, args);
      };
    },
  });
  state.auto.owned.add({ close: () => adapter.shutdown() });
  return automatic;
}
export function attachAgents(exports: Foreign): void {
  if (exports.Runner)
    replace(
      exports.Runner.prototype,
      'run',
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          const result = original.apply(this, args);
          if (
            !enabled('openai-agents') ||
            state.auto.options.captureContent === false ||
            args[2]?.stream
          )
            return result;
          return result.then((value: Foreign) => {
            try {
              const output = value.finalOutput;
              if (output !== undefined) frameworkOutputs.set(value, output);
            } catch {
              /* Never change the application's result on capture failure. */
            }
            return value;
          });
        },
    );

  if (
    typeof exports.addTraceProcessor === 'function' &&
    !installed.has(exports.addTraceProcessor)
  ) {
    installed.add(exports.addTraceProcessor);
    let attached = false;
    onActivate('openai-agents', () => {
      if (attached) return;
      exports.addTraceProcessor(processor());
      attached = true;
    });
    observed('openai-agents');
  }
  replace(
    exports,
    'addTraceProcessor',
    (original) =>
      function (this: Foreign, value: Foreign) {
        if (isManual(value)) manualAttached = true;
        return original.call(this, value);
      },
  );
  replace(
    exports,
    'setTraceProcessors',
    (original) =>
      function (this: Foreign, processors: Foreign[]) {
        manualAttached = processors.some(isManual);
        const hasConfident = processors.some(
          (p) =>
            p?.[marker] ||
            p?.constructor?.name === 'ConfidentOpenAIAgentsProcessor',
        );
        return original.call(
          this,
          !enabled('openai-agents') || hasConfident
            ? processors
            : [...processors, processor()],
        );
      },
  );
  for (const name of ['OpenAIResponsesModel', 'OpenAIChatCompletionsModel'])
    if (exports[name]) {
      suppressMethods(
        exports[name].prototype,
        ['getResponse', 'getStreamedResponse'],
        'openai-agents',
      );
    }
}
