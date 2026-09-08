import { init } from 'confident-trace';
import OpenAI from 'openai';

const tracing = init();
try {
  const client = new OpenAI();
  const result = await client.responses.create({
    model: 'gpt-4.1-mini',
    input: 'Hello',
  });
  console.log(result.output_text);
} finally {
  await tracing.shutdown();
}
