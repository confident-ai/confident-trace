import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { NodeTracerProvider } from '@opentelemetry/sdk-trace-node';
import { createSpanProcessor } from '@/index';

const exporter = new InMemorySpanExporter();
const provider = new NodeTracerProvider({
  spanProcessors: [createSpanProcessor({ exporter })],
});
provider.register();
provider.getTracer('application').startSpan('work').end();
await provider.forceFlush();
console.log(exporter.getFinishedSpans().map((span) => span.name));
// The application, not confident-trace.shutdown(), owns this lifecycle.
await provider.shutdown();
