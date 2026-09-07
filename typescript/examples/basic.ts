import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
import { init } from '@/index';

// Offline example. Omit exporter to send through the configured OTLP exporter.
const exporter = new InMemorySpanExporter();
const runtime = init({
  exporter,
  resourceAttributes: { 'service.name': 'example' },
});
await runtime.getTracer().startActiveSpan('answer', async (span) => {
  try {
    await Promise.resolve();
    span.setAttribute('example.result', 'hello');
  } finally {
    span.end();
  }
});
await runtime.flush();
console.log(exporter.getFinishedSpans().map((span) => span.name));
await runtime.shutdown();
