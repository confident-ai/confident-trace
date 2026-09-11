import { createRequire } from 'node:module';
import { context } from '@opentelemetry/api';
import { createVercelAITracer } from '@/integrations/vercel-ai';
import { enabled, failed, onActivate } from '@/auto/control';
import { state, suppress } from '@/runtime/state';
import { observed, replace } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

const requireBridge = createRequire(
  `${process.cwd()}/.confident-trace-loader.cjs`,
);
const installed = new WeakSet<object>();
const marker = Symbol.for('confident-trace.telemetry');
const tracerMarker = Symbol.for('confident-trace.vercel.tracer');
// AI SDK copies share a process-global telemetry registry. Register only once.
let integration: Foreign;
let manualGlobal = false;
function isManual(item: Foreign): boolean {
  return Boolean(!item?.[marker] && item?.tracer?.[tracerMarker]);
}
export function attachVercel(exports: Foreign, bridgePath: string): void {
  if (
    typeof exports.registerTelemetry !== 'function' ||
    installed.has(exports.registerTelemetry)
  )
    return;
  installed.add(exports.registerTelemetry);
  replace(
    exports,
    'registerTelemetry',
    (original) =>
      function (this: Foreign, ...items: Foreign[]) {
        if (items.some(isManual)) manualGlobal = true;
        return original.apply(this, items);
      },
  );
  onActivate('vercel-ai', () => {
    if (integration) return;
    let adapter: Foreign;
    let unavailable = false;
    // Loading the bridge inside the import hook can return partially initialized
    // exports: the bridge itself imports ai. Telemetry is read only at call time.
    integration = new Proxy(
      {},
      {
        get(_target, key) {
          if (key === marker) return true;
          if (typeof key !== 'string' || key === 'then') return undefined;
          if (!enabled('vercel-ai') || manualGlobal) return undefined;
          if (!adapter && !unavailable) {
            try {
              const { OpenTelemetry } = requireBridge(bridgePath);
              adapter = new OpenTelemetry({ tracer: createVercelAITracer() });
            } catch (error) {
              unavailable = true;
              failed('vercel-ai', error);
            }
          }
          if (unavailable) return undefined;
          const method = Reflect.get(adapter, key, adapter);
          if (typeof method !== 'function') return method;
          return (...args: Foreign[]) => {
            if (!enabled('vercel-ai') || manualGlobal)
              return String(key).startsWith('execute')
                ? args[0].execute()
                : undefined;
            if (key === 'executeLanguageModelCall')
              return context.with(
                context.active().setValue(suppress, true),
                () => method.apply(adapter, args),
              );
            return method.apply(adapter, args);
          };
        },
      },
    );
    exports.registerTelemetry(integration);
  });
  for (const key of [
    'generateText',
    'streamText',
    'generateObject',
    'streamObject',
  ])
    replace(
      exports,
      key,
      (original) =>
        function (this: Foreign, options: Foreign) {
          if (!enabled('vercel-ai') || options?.telemetry?.isEnabled === false)
            return original.call(this, options);
          const telemetry = { ...options?.telemetry };
          if (telemetry.integrations !== undefined) {
            const existing = Array.isArray(telemetry.integrations)
              ? telemetry.integrations
              : [telemetry.integrations];
            // An explicit OTel bridge owns tracing for this call; don't add a second.
            telemetry.integrations = existing.some(
              (i: Foreign) => i?.[marker] || isManual(i),
            )
              ? existing
              : [...existing, integration];
          }
          if (state.auto.options.captureContent === false) {
            telemetry.recordInputs = false;
            telemetry.recordOutputs = false;
          }
          // Scope only the provider model methods, leaving tool execution untouched.
          const model = options.model;
          const tracedModel =
            model && typeof model === 'object'
              ? new Proxy(model, {
                  get(target, prop) {
                    const value = Reflect.get(target, prop, target);
                    if (
                      ['doGenerate', 'doStream'].includes(String(prop)) &&
                      typeof value === 'function'
                    ) {
                      return (...args: Foreign[]) =>
                        context.with(
                          context.active().setValue(suppress, true),
                          () => value.apply(target, args),
                        );
                    }
                    return typeof value === 'function'
                      ? value.bind(target)
                      : value;
                  },
                })
              : model;
          return original.call(this, {
            ...options,
            model: tracedModel,
            telemetry,
          });
        },
    );
  observed('vercel-ai');
}
