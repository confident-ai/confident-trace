import assert from 'node:assert/strict';
import { init, turn, traceContext } from 'confident-trace';
import { InMemorySpanExporter } from '@opentelemetry/sdk-trace-base';
const sink = new InMemorySpanExporter();
const runtime = init({ exporter: sink });
const { Agent, Runner, Usage, setTraceProcessors } =
  await import('@openai/agents');
setTraceProcessors([]);
const agent = new Agent({
  name: 'test',
  model: {
    getResponse: async () => ({
      usage: new Usage(),
      output: [
        {
          type: 'message',
          role: 'assistant',
          status: 'completed',
          content: [{ type: 'output_text', text: 'Agent answer' }],
        },
      ],
    }),
  },
});
const agentResult = await turn(
  { name: 'agents-result', threadId: 'test' },
  () => new Runner().run(agent, 'Hi'),
);
assert.equal(agentResult.finalOutput, 'Agent answer');
const { BaseChatModel } =
  await import('@langchain/core/language_models/chat_models');
const { AIMessage } = await import('@langchain/core/messages');
const { createAgent } = await import('langchain');
const { tool } = await import('@langchain/core/tools');

class WeatherModel extends BaseChatModel {
  _llmType() {
    return 'weather-model';
  }
  bindTools() {
    return this;
  }
  async _generate(messages) {
    const finished = messages.some((m) => m.type === 'tool');
    const message = new AIMessage(
      finished
        ? { content: 'Sunny in SF' }
        : {
            content: '',
            tool_calls: [
              {
                name: 'get_weather',
                args: { city: 'SF' },
                id: 'call-1',
                type: 'tool_call',
              },
            ],
          },
    );
    return { generations: [{ text: message.content, message }] };
  }
}
const graph = createAgent({
  model: new WeatherModel({}),
  tools: [
    tool(({ city }) => `Sunny in ${city}`, {
      name: 'get_weather',
      description: 'Weather',
      schema: {
        type: 'object',
        properties: { city: { type: 'string' } },
        required: ['city'],
      },
    }),
  ],
});
await graph.invoke({ messages: [{ role: 'user', content: 'Weather?' }] });
await traceContext({ name: 'Explicit graph name' }, () =>
  graph.invoke({ messages: [{ role: 'user', content: 'Weather?' }] }),
);
const graphResult = await turn({ name: 'graph-result', threadId: 'test' }, () =>
  graph.invoke({ messages: [{ role: 'user', content: 'Weather?' }] }),
);
assert.equal(graphResult.messages.at(-1).content, 'Sunny in SF');
await runtime.flush();
const spans = sink.getFinishedSpans();
const roots = spans.filter((s) => !s.parentSpanContext);
assert.equal(roots.length, 4);
for (const root of roots) {
  assert.equal(
    root.attributes['confident.trace.name'],
    root === roots[2] ? 'Explicit graph name' : root.name,
  );
}
assert.equal(
  roots.find((s) => s.name === 'agents-result').attributes[
    'confident.trace.output'
  ],
  JSON.stringify('Agent answer'),
);
const graphOutput = roots.find((s) => s.name === 'graph-result').attributes[
  'confident.trace.output'
];
assert.ok(graphOutput.includes('Sunny in SF'));
assert.ok(!graphOutput.includes('[unsupported]'), graphOutput);
const tools = spans.filter((s) => s.name === 'get_weather');
assert.equal(tools.length, 3);
for (const span of tools)
  assert.equal(
    span.attributes['confident.span.output'],
    JSON.stringify('Sunny in SF'),
  );
await runtime.shutdown();
console.log('Agent results, graph state, tool output and root names passed');
