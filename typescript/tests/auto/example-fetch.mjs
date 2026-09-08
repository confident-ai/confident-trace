/* global Request, Response */
// Network-free model responses for the public examples. No instrumentation setup.
globalThis.fetch = async (input) => {
  const url = String(input instanceof Request ? input.url : input);
  if (url.includes('/traces')) return new Response(null, { status: 204 });
  if (url.includes('anthropic'))
    return Response.json({
      id: 'msg_1',
      type: 'message',
      role: 'assistant',
      model: 'test',
      content: [{ type: 'text', text: 'Hello' }],
      stop_reason: 'end_turn',
      stop_sequence: null,
      usage: { input_tokens: 2, output_tokens: 1 },
    });
  if (url.includes('google'))
    return Response.json({
      candidates: [
        {
          content: { role: 'model', parts: [{ text: 'Hello' }] },
          finishReason: 'STOP',
        },
      ],
      usageMetadata: {
        promptTokenCount: 2,
        candidatesTokenCount: 1,
        totalTokenCount: 3,
      },
      modelVersion: 'test',
    });
  if (url.includes('/chat/completions'))
    return Response.json({
      id: 'chatcmpl_1',
      object: 'chat.completion',
      created: 1,
      model: 'gpt-4.1-mini',
      choices: [
        {
          index: 0,
          message: { role: 'assistant', content: 'Hello' },
          finish_reason: 'stop',
          logprobs: null,
        },
      ],
      usage: { prompt_tokens: 2, completion_tokens: 1, total_tokens: 3 },
    });
  if (url.includes('/responses'))
    return Response.json({
      id: 'resp_1',
      object: 'response',
      created_at: 1,
      status: 'completed',
      model: 'gpt-4.1-mini',
      output: [
        {
          id: 'msg_1',
          type: 'message',
          role: 'assistant',
          status: 'completed',
          content: [{ type: 'output_text', text: 'Hello', annotations: [] }],
        },
      ],
      usage: {
        input_tokens: 2,
        output_tokens: 1,
        total_tokens: 3,
        input_tokens_details: { cached_tokens: 0 },
        output_tokens_details: { reasoning_tokens: 0 },
      },
      error: null,
      incomplete_details: null,
    });
  throw new Error(
    `Unexpected example network request: ${new URL(url).hostname}`,
  );
};
