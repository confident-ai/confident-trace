import { init } from 'confident-trace';
import { Mastra } from '@mastra/core';
import { Agent } from '@mastra/core/agent';

const tracing = init();
try {
  const assistant = new Agent({
    id: 'assistant',
    name: 'Assistant',
    instructions: 'Be helpful.',
    model: 'openai/gpt-4.1-mini',
  });
  const mastra = new Mastra({ agents: { assistant }, logger: false });
  const result = await mastra.getAgent('assistant').generate('Hello');
  console.log(result.text);
} finally {
  await tracing.shutdown();
}
