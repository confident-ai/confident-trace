/** Logical role, independent of OTel SpanKind and the integration's SDK name. */
export type IntegrationSpanType =
  'llm' | 'agent' | 'tool' | 'retriever' | 'custom';

export function mastraSpanType(type: string): IntegrationSpanType {
  if (type === 'model_generation' || type === 'model_inference') return 'llm';
  if (type === 'agent_run') return 'agent';
  if (type === 'tool_call' || type.endsWith('_tool_call')) return 'tool';
  return 'custom';
}

export function vercelSpanType(
  name: string,
  operation: unknown,
): IntegrationSpanType {
  if (
    operation === 'execute_tool' ||
    name === 'ai.toolCall' ||
    name.startsWith('execute_tool ')
  )
    return 'tool';
  if (
    operation === 'chat' ||
    operation === 'generate_content' ||
    /\.(doGenerate|doStream)$/.test(name) ||
    name.startsWith('chat ')
  )
    return 'llm';
  if (/^step(?: |$)/.test(name)) return 'custom';
  if (
    operation === 'invoke_agent' ||
    name.startsWith('invoke_agent ') ||
    /^ai\.(generateText|streamText|generateObject|streamObject)$/.test(name)
  )
    return 'agent';
  return 'custom';
}
