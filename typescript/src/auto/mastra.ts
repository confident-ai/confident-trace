import { createRequire } from 'node:module';
import { ConfidentMastraExporter } from '@/integrations/mastra';
import { state } from '@/runtime/state';
import { onActivate } from '@/auto/control';
import { observed, wrapConstructor, setExport } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

// Loaded only after Mastra is encountered and tracing is activated.
const requireBridge = createRequire(
  `${process.cwd()}/.confident-trace-loader.cjs`,
);
const instances = new WeakSet<object>();
export function attachMastra(exports: Foreign, bridgePath: string): void {
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
