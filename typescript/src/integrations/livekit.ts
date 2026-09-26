import { readFile, stat } from 'node:fs/promises';
import { context, diag, trace } from '@opentelemetry/api';
import type { Span } from '@opentelemetry/api';
import { isRoutedSpan } from '@/runtime/scopes';
import { state } from '@/runtime/state';

export const SCOPE = 'livekit-agents';
// LiveKit calls the model SDK inside this retry-attempt span; its parent,
// llm_request, carries the GenAI operation.
const INFERENCE_SPANS = new Set(['llm_request_run']);
type ScopedSpan = { name?: string; instrumentationScope?: { name: string } };

/** LiveKit already records this model call on the provider we export. */
export function liveKitOwnsCall(): boolean {
  const current = trace.getSpan(context.active()) as
    (Span & ScopedSpan) | undefined;
  return Boolean(
    current &&
    current.instrumentationScope?.name === SCOPE &&
    state.integrationScopes.has(SCOPE) &&
    INFERENCE_SPANS.has(current.name ?? '') &&
    isRoutedSpan(current),
  );
}

// About what the upload bound carries at 50 Mbps, roughly 25 minutes of call.
const MAX_RECORDING_BYTES = 18 * 1024 * 1024;
// Leaves the final span flush its own share of LiveKit's 10s shutdown budget.
const UPLOAD_TIMEOUT_MS = 3000;
const SPAN_BATCH_PATH = '/v1/traces';
const CALL_RECORDING_PATH = '/v1/call-recordings';
// LiveKit's own PII opt-out (allowPiiFromEnv in @livekit/agents telemetry).
const ALLOW_PII_ENV_VAR = 'LIVEKIT_TELEMETRY_ALLOW_PII';
const FALSY = new Set(['0', 'false', 'no', 'off']);

function recordingEndpoint():
  { url: string; headers: Record<string, string> } | undefined {
  const target = state.otlpHttpExport;
  if (!target?.endpoint.endsWith(SPAN_BATCH_PATH)) return undefined;
  return {
    url:
      target.endpoint.slice(0, -SPAN_BATCH_PATH.length) + CALL_RECORDING_PATH,
    headers: target.headers,
  };
}

function piiWithheld(): boolean {
  const raw = process.env[ALLOW_PII_ENV_VAR];
  return raw !== undefined && FALSY.has(raw.trim().toLowerCase());
}

type SessionReport = {
  audioRecordingPath?: string;
  audioRecordingStartedAt?: number;
};
export type LiveKitJobContext = {
  job: { room?: { sid?: string } };
  makeSessionReport(): SessionReport;
  addShutdownCallback(callback: () => Promise<void>): void;
};

/** Send the call audio LiveKit recorded itself (`record: true`) to Confident. */
export async function uploadCallRecording(
  ctx: LiveKitJobContext,
): Promise<void> {
  const target = recordingEndpoint();
  if (!target || !state.runtime?.active || !state.policy?.enabled) return;
  if (piiWithheld()) return;
  try {
    const report = ctx.makeSessionReport();
    const path = report.audioRecordingPath;
    const startedAt = report.audioRecordingStartedAt;
    const roomSid = ctx.job.room?.sid;
    if (!path || startedAt === undefined || !roomSid) return;
    if ((await stat(path)).size > MAX_RECORDING_BYTES) {
      diag.warn('LiveKit call recording is too large to upload');
      return;
    }
    const query = new URLSearchParams({
      threadId: roomSid,
      startedAt: String(Math.round(startedAt)),
    });
    const response = await fetch(`${target.url}?${query}`, {
      method: 'POST',
      headers: { ...target.headers, 'content-type': 'audio/ogg' },
      body: await readFile(path),
      signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
    });
    if (!response.ok) diag.warn('LiveKit call recording upload failed');
  } catch {
    diag.warn('LiveKit call recording upload failed or timed out');
  }
}
