import { context, ProxyTracerProvider, trace } from '@opentelemetry/api';
import type { Span } from '@opentelemetry/api';
import { isRoutedSpan } from '@/runtime/scopes';
import { state } from '@/runtime/state';

/** Google ADK owns its spans; this scope is ADK's, not Confident's. */
export const SCOPE = 'gcp.vertex.agent';
const OPERATION = 'gen_ai.operation.name';
const INFERENCE_OPERATIONS = new Set(['generate_content', 'chat']);
// ADK's content routing knob, shared with other OTel GenAI instrumentations.
// ADK JS leaves content off spans unless it names a span-bearing mode.
export const CAPTURE_MESSAGE_CONTENT =
  'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT';
const SPAN_ONLY = 'SPAN_ONLY';
// ADK's legacy span-content knob; an explicit "false" is a content opt-out.
const LEGACY_CONTENT_IN_SPANS = 'ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS';
type ScopedSpan = {
  instrumentationScope?: { name: string };
  attributes?: Record<string, unknown>;
};

/** ADK already records this model call on the provider we export. */
export function adkOwnsCall(): boolean {
  const current = trace.getSpan(context.active()) as
    (Span & ScopedSpan) | undefined;
  return Boolean(
    current &&
    current.instrumentationScope?.name === SCOPE &&
    state.integrationScopes.has(SCOPE) &&
    INFERENCE_OPERATIONS.has(String(current.attributes?.[OPERATION])) &&
    isRoutedSpan(current),
  );
}

/** ADK starts its spans on the global provider; whether that one is ours. */
function adkSpansReachUs(): boolean {
  const owned = state.ownedProvider?.tracerProvider;
  const global = trace.getTracerProvider();
  const delegate =
    global instanceof ProxyTracerProvider ? global.getDelegate() : global;
  return Boolean(owned && delegate === owned);
}

/**
 * Put ADK's model and tool IO on its spans when content capture is on and the
 * application has not chosen. ADK reads the knob per call, so runs started
 * after init() are covered. Returns how to undo it, if anything was set.
 */
export function enableSpanContent(): (() => void) | undefined {
  if (!state.policy?.enabled || !adkSpansReachUs()) return undefined;
  // Any value the application set, including an empty one, is theirs.
  if (process.env[CAPTURE_MESSAGE_CONTENT] !== undefined) return undefined;
  const legacy = process.env[LEGACY_CONTENT_IN_SPANS]?.trim().toLowerCase();
  if (legacy === 'false' || legacy === '0') return undefined;
  process.env[CAPTURE_MESSAGE_CONTENT] = SPAN_ONLY;
  return () => {
    if (process.env[CAPTURE_MESSAGE_CONTENT] === SPAN_ONLY)
      delete process.env[CAPTURE_MESSAGE_CONTENT];
  };
}
