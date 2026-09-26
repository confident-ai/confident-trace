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

const MAX_RECORDING_BYTES = 50 * 1024 * 1024;
const UPLOAD_TIMEOUT_MS = 5000;

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
  const target = state.recordingUpload;
  if (!target || !state.runtime?.active || !state.policy?.enabled) return;
  try {
    const report = ctx.makeSessionReport();
    const path = report.audioRecordingPath;
    const startedAt = report.audioRecordingStartedAt;
    const roomSid = ctx.job.room?.sid;
    if (!path || startedAt === undefined || !roomSid) return;
    if ((await stat(path)).size > MAX_RECORDING_BYTES) {
      diag.debug('LiveKit call recording is too large to upload');
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
    if (!response.ok) diag.debug('LiveKit call recording upload failed');
  } catch {
    diag.debug('LiveKit call recording upload failed or timed out');
  }
}
