import { init } from 'confident-trace';
import {
  StateGraph,
  MessagesAnnotation,
  START,
  END,
} from '@langchain/langgraph';
import { ChatOpenAI } from '@langchain/openai';

const tracing = init();
try {
  const model = new ChatOpenAI({ model: 'gpt-4.1-mini' });
  const graph = new StateGraph(MessagesAnnotation)
    .addNode('answer', async (state) => ({
      messages: [await model.invoke(state.messages)],
    }))
    .addEdge(START, 'answer')
    .addEdge('answer', END)
    .compile();
  const result = await graph.invoke({
    messages: [{ role: 'user', content: 'Hello' }],
  });
  console.log(result.messages.at(-1)?.content);
} finally {
  await tracing.shutdown();
}
