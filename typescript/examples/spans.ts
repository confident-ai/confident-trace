import { init, shutdown, span, turn, updateSpan, updateTrace } from '@/index';

// Offline application logic; exports to your configured OTLP endpoint.
const retrieve = span(
  { name: 'retrieve', type: 'retriever', metadata: { index: 'support' } },
  async (query: string) => {
    const documents = ['Refunds are available within 30 days.'];
    updateSpan({ input: query, retrievalContext: documents });
    return documents;
  },
);
const answer = span(
  { name: 'answer', type: 'agent' },
  async function* (query: string) {
    updateSpan({ input: query });
    const documents = await retrieve(query);
    const output = documents[0]!;
    for (const word of output.split(' ')) yield word;
    updateSpan({ output, retrievalContext: documents });
    updateTrace({ output, retrievalContext: documents });
  },
);
init();
try {
  await turn(
    { threadId: 'chat-42', input: 'Can I get a refund?' },
    async () => {
      for await (const word of answer('Can I get a refund?')) console.log(word);
    },
  );
} finally {
  await shutdown();
}
