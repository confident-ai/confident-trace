import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createServer } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { build } from 'esbuild';
import protobuf from 'protobufjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const protoRoot = new protobuf.Root();
protoRoot.resolvePath = (_origin, target) =>
  join(root, 'tests/support/proto', target);
await protoRoot.load(
  'opentelemetry/proto/collector/trace/v1/trace_service.proto',
);
const requestType = protoRoot.lookupType(
  'opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest',
);
let received = [];
const server = createServer(async (req, res) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  let bytes = Buffer.concat(chunks);
  if (req.headers['content-encoding'] === 'gzip') bytes = gunzipSync(bytes);
  const decoded = requestType.toObject(requestType.decode(bytes));
  for (const resource of decoded.resourceSpans ?? [])
    for (const scope of resource.scopeSpans ?? [])
      received.push(...(scope.spans ?? []));
  res.writeHead(200, { 'content-type': 'application/x-protobuf' });
  res.end();
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const directory = await mkdtemp(join(root, '.auto-examples-'));
const integrations = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  'google-genai': 'Google GenAI',
  'vercel-ai': 'Vercel AI SDK',
  langchain: 'LangChain',
  langgraph: 'LangGraph',
  mastra: 'Mastra',
  'openai-agents': 'OpenAI Agents SDK',
};
try {
  for (const [name, integration] of Object.entries(integrations)) {
    received = [];
    const outfile = join(directory, `${name}.mjs`);
    await build({
      entryPoints: [join(root, `examples/auto/${name}.ts`)],
      outfile,
      bundle: true,
      packages: 'external',
      external: ['confident-trace', 'confident-trace/*'],
      platform: 'node',
      format: 'esm',
    });
    const { stdout, stderr } = await promisify(execFile)(
      process.execPath,
      [
        '--import',
        './tests/auto/example-fetch.mjs',
        '--import',
        'confident-trace/register',
        outfile,
      ],
      {
        cwd: root,
        timeout: 60000,
        env: {
          ...process.env,
          OPENAI_API_KEY: 'test',
          ANTHROPIC_API_KEY: 'test',
          ANTHROPIC_MODEL: 'test',
          GOOGLE_API_KEY: 'test',
          GOOGLE_MODEL: 'test',
          OTEL_SDK_DISABLED: 'false',
          OTEL_EXPORTER_OTLP_PROTOCOL: 'http/protobuf',
          OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: `http://127.0.0.1:${server.address().port}/v1/traces`,
        },
      },
    );
    assert.match(stdout, /Hello/, `${name}: unexpected example output`);
    assert.ok(!stderr.includes('[confident-trace]'), `${name}: ${stderr}`);
    const fields = (span) =>
      Object.fromEntries(
        (span.attributes ?? []).map((a) => [a.key, a.value.stringValue]),
      );
    assert.ok(
      received.some(
        (s) => fields(s)['confident.span.integration'] === integration,
      ),
      `${name}: no exported spans`,
    );
    const modelSpans = received.filter(
      (s) => fields(s)['confident.span.type'] === 'llm',
    );
    // Mastra emits model step/inference spans as distinct native lifecycle events.
    if (name !== 'mastra')
      assert.equal(
        modelSpans.length,
        1,
        `${name}: duplicate or absent model spans: ${JSON.stringify(received.map((s) => [s.name, fields(s)]))}`,
      );
    console.log(
      `${name}: public entry example exported ${received.length} spans`,
    );
  }
} finally {
  await new Promise((resolve) => server.close(resolve));
  await rm(directory, { recursive: true, force: true });
}
