import { init } from 'confident-trace';
import { ChatPromptTemplate } from '@langchain/core/prompts';
import { ChatOpenAI } from '@langchain/openai';

const tracing = init();
try {
  const prompt = ChatPromptTemplate.fromTemplate('Explain {topic} simply.');
  const model = new ChatOpenAI({ model: 'gpt-4.1-mini' });
  const chain = prompt.pipe(model);
  const result = await chain.invoke({ topic: 'gravity' });
  console.log(result.content);
} finally {
  await tracing.shutdown();
}
