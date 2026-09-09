import { init } from 'confident-trace';
import { createSpanProcessor } from 'confident-trace/otel';
import type { InitOptions, TraceRuntime } from 'confident-trace';
import { SCHEMA_URL } from 'confident-trace/semconv';
import type { GenAiMessage } from 'confident-trace/semconv';
import type { SpanProcessor } from '@opentelemetry/sdk-trace-base';

const options: InitOptions = {
  timeoutMillis: 1000,
  captureContent: false,
  resourceAttributes: { 'service.name': 'consumer' },
};
const runtime: TraceRuntime = init(options);
const tracer = runtime.getTracer();
tracer.startSpan(SCHEMA_URL).end();
const processor: SpanProcessor = createSpanProcessor();
void processor;
const message: GenAiMessage = {
  role: 'user',
  parts: [{ type: 'text', content: 'hello' }],
};
void message;
// @ts-expect-error Python-style option spelling is intentionally unsupported.
init({ api_key: 'no' });
// @ts-expect-error Protocol is a closed set of supported transports.
init({ protocol: 'http/json' });
// @ts-expect-error Runtime state is readonly.
runtime.active = false;

// Provider subpaths remain usable without installing unrelated SDK type packages.
import { instrumentOpenAI } from 'confident-trace/openai';
import { instrumentAnthropic } from 'confident-trace/anthropic';
import { instrumentGoogleGenAI } from 'confident-trace/google-genai';
const create = async (request: { model: string }) => ({ model: request.model });
const restore: () => void = instrumentOpenAI(
  { chat: { completions: { create } }, responses: { create } },
  { captureContent: false },
);
restore();
instrumentAnthropic({ messages: { create, stream: create } });
instrumentGoogleGenAI({
  models: { generateContent: create, generateContentStream: create },
});
// @ts-expect-error A client must expose the instrumented resource surfaces.
instrumentOpenAI({});

import { createVercelAITracer } from 'confident-trace/vercel-ai';
import { ConfidentMastraExporter } from 'confident-trace/mastra';
const frameworkTracer = createVercelAITracer({ captureContent: false });
frameworkTracer.startActiveSpan('typed', (span) => span.end());
const mastra = new ConfidentMastraExporter({ maxContentBytes: 512 });
const mastraFlush: Promise<void> = mastra.flush();
void mastraFlush;
// @ts-expect-error Timeout options are numeric milliseconds.
new ConfidentMastraExporter({ timeoutMillis: '5s' });

import { ConfidentLangChainCallbackHandler } from 'confident-trace/langchain';
import { ConfidentLangGraphCallbackHandler } from 'confident-trace/langgraph';
import { ConfidentOpenAIAgentsProcessor } from 'confident-trace/openai-agents';
new ConfidentLangChainCallbackHandler({ captureContent: false }).close();
new ConfidentLangGraphCallbackHandler({ rootSpanType: 'agent' }).close();
const agentsProcessor = new ConfidentOpenAIAgentsProcessor({
  tracer,
  flush: async () => {},
});
void agentsProcessor.shutdown();
// @ts-expect-error Callback capacity is a number.
new ConfidentLangChainCallbackHandler({ maxActiveSpans: 'many' });

import { span, withSpan, turn, updateSpan, updateTrace } from 'confident-trace';
const add = span({ type: 'tool' }, (a: number, b: number) => a + b);
const sum: number = add(1, 2);
const asyncValue: Promise<string> = withSpan(
  { type: 'agent' },
  async () => 'answer',
);
const identity = span({}, <T,>(value: T): T => value);
const genericValue: string = identity('typed');
const produce = span({ type: 'llm' }, async function* (query: string) {
  yield query;
});
const iterable: AsyncGenerator<string, void, unknown> = produce('hello');
const syncProduce = span({}, function* (): Generator<number, string, boolean> {
  yield 1;
  return 'done';
});
const generator: Generator<number, string, boolean> = syncProduce();
const turnValue: number = turn({ threadId: 'chat' }, () => 2);
updateSpan({ output: null, retrievalContext: ['document'] });
updateTrace({ userId: 'user', toolsCalled: [{ name: 'lookup' }] });
void [sum, asyncValue, genericValue, iterable, generator, turnValue];
// @ts-expect-error Invalid call argument must not be widened by wrapping.
add('1', 2);
// @ts-expect-error Unsupported backend role.
span({ type: 'workflow' }, () => 1);
// @ts-expect-error Trace metadata belongs to updateTrace, not options.
span({ userId: 'user' }, () => 1);
// @ts-expect-error Turns require a conversation identifier.
turn({}, () => 1);

// @ts-expect-error Unknown span option must be rejected by IDEs.
span({ anything: true }, () => 1);
// @ts-expect-error Unknown update field must be rejected by IDEs.
updateSpan({ anything: true });
// @ts-expect-error Thread fields are a closed typed object.
updateTrace({ thread: { unknown: true } });

// @ts-expect-error Model usage fields require an explicitly typed LLM span.
span({ type: 'tool', model: 'test' }, () => 1);

updateSpan({ output: 'answer', model: 'test', inputTokenCount: 0 });
// @ts-expect-error Token counts must be numbers.
updateSpan({ inputTokenCount: 'five' });
