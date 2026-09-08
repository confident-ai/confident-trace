import { init } from 'confident-trace';
import Anthropic from '@anthropic-ai/sdk';

const tracing = init();
try {
  const client = new Anthropic();
  const result = await client.messages.create({
    model: process.env.ANTHROPIC_MODEL!,
    max_tokens: 100,
    messages: [{ role: 'user', content: 'Hello' }],
  });
  console.log(result.content);
} finally {
  await tracing.shutdown();
}
