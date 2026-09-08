/* global Response */
import assert from 'node:assert/strict';
import { Worker, isMainThread, parentPort } from 'node:worker_threads';
import { createRequire } from 'node:module';
import { init } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const sink = new InMemorySpanExporter();
const rt = init({ exporter: sink });
assert.equal(rt.getInstrumentationStatus().integrations.openai, 'not observed');
const { default: OpenAI } = await import('openai');
const CommonJSOpenAI = createRequire(import.meta.url)('openai');
for (const Client of [OpenAI, CommonJSOpenAI]) {
  const client = new Client({
    apiKey: 'test',
    fetch: async () => Response.json({ id: 'r', model: 'test', output: [] }),
  });
  await client.responses.create({ model: 'test', input: 'Hi' });
}
await rt.flush();
assert.equal(sink.getFinishedSpans().length, 2);
assert.equal(rt.getInstrumentationStatus().integrations.openai, 'enabled');
if (isMainThread) {
  const worker = new Worker(new URL(import.meta.url));
  const count = await new Promise((resolve, reject) => {
    worker.once('message', resolve);
    worker.once('error', reject);
    worker.once('exit', (code) => {
      if (code) reject(new Error(`worker exit ${code}`));
    });
  });
  assert.equal(count, 2);
  assert.equal(
    sink.getFinishedSpans().length,
    2,
    'worker runtime must be independent',
  );
} else parentPort.postMessage(sink.getFinishedSpans().length);
await rt.shutdown();
console.log('Dynamic imports, mixed modules, and worker initialization passed');
