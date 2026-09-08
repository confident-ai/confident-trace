import { init } from 'confident-trace';
import { generateText } from 'ai';
import { openai } from '@ai-sdk/openai';

const tracing = init();
try {
  const result = await generateText({
    model: openai('gpt-4.1-mini'),
    prompt: 'Hello',
  });
  console.log(result.text);
} finally {
  await tracing.shutdown();
}
