import { context, trace, isSpanContextValid } from '@opentelemetry/api';
import { createRequire } from 'node:module';
import { ConfidentMastraExporter } from '@/integrations/mastra';
import { state, frameworkOutputs } from '@/runtime/state';
import { enabled, onActivate } from '@/auto/control';
import { observed, replace, wrapConstructor, setExport } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

// Loaded only after Mastra is encountered and tracing is activated.
const requireBridge = createRequire(
  `${process.cwd()}/.confident-trace-loader.cjs`,
);
const instances = new WeakSet<object>();
export function attachMastra(exports: Foreign, bridgePath: string): void {
  if (exports.Agent)
    replace(
      exports.Agent.prototype,
      'generate',
      (original) =>
        function (this: Foreign, ...args: Foreign[]) {
          // Mastra uses its own tracing context; bridge an enclosing OTel turn
          // through its public correlation options before execution starts.
          const parent = trace.getSpan(context.active());
          if (enabled('mastra') && parent?.isRecording()) {
            const ids = parent.spanContext();
            const options = args[1] ?? {};
            const tracingOptions = options.tracingOptions ?? {};
            if (
              isSpanContextValid(ids) &&
              tracingOptions.traceId === undefined &&
              tracingOptions.parentSpanId === undefined &&
              !options.tracingContext?.currentSpan
            )
              args[1] = {
                ...options,
                tracingOptions: {
                  ...tracingOptions,
                  traceId: ids.traceId,
                  parentSpanId: ids.spanId,
                },
              };
          }
          const result = original.apply(this, args);
          if (!enabled('mastra') || state.auto.options.captureContent === false)
            return result;
          return result.then((value: Foreign) => {
            try {
              const output = value.object ?? value.text;
              if (output !== undefined) frameworkOutputs.set(value, output);
            } catch {
              /* Never change the application's result on capture failure. */
            }
            return value;
          });
        },
    );

  if (typeof exports.Mastra !== 'function') return;
  setExport(
    exports,
    'Mastra',
    wrapConstructor(exports.Mastra, (instance) => {
      if (instances.has(instance)) return;
      instances.add(instance);
      const ref = new WeakRef(instance);
      let attached = false;
      onActivate('mastra', () => {
        const mastra = ref.deref();
        if (!mastra || attached) return;
        const existing = mastra.observability?.getDefaultInstance?.();
        if (
          existing
            ?.getExporters?.()
            .some((e: Foreign) => e.name === 'confident-trace')
        ) {
          attached = true;
          return;
        }
        const options = { ...state.auto.options };
        // The runtime owns an injected exporter. Adapter processors may export to it,
        // but must not shut it down independently.
        if (options.exporter) {
          const owner = options.exporter;
          options.exporter = {
            export: (spans, done) => owner.export(spans, done),
            shutdown: async () => {},
          };
        }
        const exporter = new ConfidentMastraExporter(options);
        try {
          const { Observability } = requireBridge(bridgePath);
          const entrypoint = new Observability({
            configs: {
              default: {
                serviceName: String(
                  options.resourceAttributes?.['service.name'] ??
                    process.env.OTEL_SERVICE_NAME ??
                    'confident-trace',
                ),
                exporters: [exporter],
              },
            },
          });
          const fallback = entrypoint.getDefaultInstance();
          entrypoint.unregisterInstance('default');
          mastra.registerExporter(exporter, fallback, entrypoint);
          state.auto.owned.add({
            flush: async () => {
              await mastra.observability.flush();
              await exporter.flush();
            },
            close: async () => {
              await mastra.observability.flush();
              await exporter.shutdown();
            },
          });
          attached = true;
        } catch (error) {
          void exporter.shutdown().catch(() => {});
          throw error;
        }
      });
    }),
  );
  observed('mastra');
}
