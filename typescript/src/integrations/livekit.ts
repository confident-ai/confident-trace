import { readFile, stat } from 'node:fs/promises';
import { request as httpRequest } from 'node:http';
import type { IncomingMessage } from 'node:http';
import { request as httpsRequest } from 'node:https';
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
  | { url: string; headers: Record<string, string>; tlsSkipVerify: boolean }
  | undefined {
  const target = state.otlpHttpExport;
  if (!target?.endpoint.endsWith(SPAN_BATCH_PATH)) return undefined;
  return {
    url:
      target.endpoint.slice(0, -SPAN_BATCH_PATH.length) + CALL_RECORDING_PATH,
    headers: target.headers,
    tlsSkipVerify: target.tlsSkipVerify,
  };
}

function piiWithheld(): boolean {
  const raw = process.env[ALLOW_PII_ENV_VAR];
  return raw !== undefined && FALSY.has(raw.trim().toLowerCase());
}

function post(
  url: string,
  headers: Record<string, string>,
  body: Buffer,
  tlsSkipVerify: boolean,
): Promise<boolean> {
  const options = {
    method: 'POST',
    headers: { ...headers, 'content-length': String(body.length) },
    rejectUnauthorized: !tlsSkipVerify,
    signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
  };
  return new Promise((resolve, reject) => {
    const onResponse = (response: IncomingMessage) => {
      response.resume();
      const status = response.statusCode ?? 0;
      resolve(status >= 200 && status < 300);
    };
    const request = url.startsWith('https:')
      ? httpsRequest(url, options, onResponse)
      : httpRequest(url, options, onResponse);
    request.on('error', reject).end(body);
  });
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
  traceUuid: string,
): Promise<void> {
  const target = recordingEndpoint();
  if (!target || !state.runtime?.active || !state.policy?.enabled) return;
  if (piiWithheld()) return;
  try {
    const report = ctx.makeSessionReport();
    const path = report.audioRecordingPath;
    const startedAt = report.audioRecordingStartedAt;
    if (!path || startedAt === undefined) return;
    if ((await stat(path)).size > MAX_RECORDING_BYTES) {
      diag.warn('LiveKit call recording is too large to upload');
      return;
    }
    const query = new URLSearchParams({
      traceUuid,
      startedAt: String(Math.round(startedAt)),
    });
    const uploaded = await post(
      `${target.url}?${query}`,
      { ...target.headers, 'content-type': 'audio/ogg' },
      await readFile(path),
      target.tlsSkipVerify,
    );
    if (!uploaded) diag.warn('LiveKit call recording upload failed');
  } catch {
    diag.warn('LiveKit call recording upload failed or timed out');
  }
}
