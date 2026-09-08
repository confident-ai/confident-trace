import { init } from 'confident-trace';
import { Agent, run } from '@openai/agents';

const tracing = init();
try {
  const agent = new Agent({
    name: 'Assistant',
    instructions: 'Be helpful.',
    model: 'gpt-4.1-mini',
  });
  const result = await run(agent, 'Hello');
  console.log(result.finalOutput);
} finally {
  await tracing.shutdown();
}
