import { diag, ProxyTracerProvider } from '@opentelemetry/api';
import { enabled, failed, onActivate } from '@/auto/control';
import { observed, replace } from '@/auto/patch';
import type { Foreign } from '@/auto/patch';
import { SCOPE, uploadCallRecording } from '@/integrations/livekit';
import type { LiveKitJobContext } from '@/integrations/livekit';
import { state } from '@/runtime/state';
import { INTEGRATIONS } from '@/semconv/generated';

type GetJobContext = (required: false) => LiveKitJobContext | undefined;
// The ESM and CJS builds each keep their own job context store.
const getJobContextCopies = new Set<GetJobContext>();
const jobsWithRecordingUpload = new WeakSet<LiveKitJobContext>();

function registerRecordingUpload(): void {
  let ctx: LiveKitJobContext | undefined;
  for (const getJobContext of getJobContextCopies) {
    ctx = getJobContext(false);
    if (ctx) break;
  }
  if (!ctx || jobsWithRecordingUpload.has(ctx)) return;
  jobsWithRecordingUpload.add(ctx);
  // Shutdown callbacks run after the session closes its recorder.
  const job = ctx;
  job.addShutdownCallback(() => uploadCallRecording(job));
}

export function attachLiveKit(exports: Foreign, modulePath = ''): void {
  if (typeof exports.getJobContext === 'function')
    getJobContextCopies.add(exports.getJobContext);
  if (typeof exports.AgentSession?.prototype?.start === 'function')
    replace(
      exports.AgentSession.prototype,
      'start',
      (original) =>
        async function (this: Foreign, ...args: Foreign[]) {
          const result = await original.apply(this, args);
          if (enabled('livekit')) {
            try {
              registerRecordingUpload();
            } catch {
              diag.debug('LiveKit call recording upload unavailable');
            }
          }
          return result;
        },
    );
  if (
    /[/\\]job_lifecycle\.[cm]?js$/.test(modulePath) &&
    typeof exports.flushJobLogs !== 'function'
  ) {
    failed('livekit');
    return;
  }
  // The worker awaits this lifecycle stage after session finalization and all
  // concurrent shutdown callbacks, including jobs that never connected.
  if (typeof exports.flushJobLogs === 'function')
    replace(
      exports,
      'flushJobLogs',
      (original) =>
        async function (this: Foreign, ...args: Foreign[]) {
          try {
            return await original.apply(this, args);
          } finally {
            if (enabled('livekit')) {
              try {
                if (!(await state.runtime?.flush(5000)))
                  diag.debug('LiveKit final span flush failed or timed out');
              } catch {
                diag.debug('LiveKit final span flush failed or timed out');
              }
            }
          }
        },
    );
  const telemetry = exports.telemetry ?? exports;
  if (typeof telemetry.setTracerProvider !== 'function' || !telemetry.tracer)
    return;
  observed('livekit');
  onActivate('livekit', () => {
    state.integrationScopes.set(SCOPE, INTEGRATIONS.livekit);
    // LiveKit Cloud replaces an unset provider with a private one. Preserve a
    // configured provider; ours keeps Cloud export via registerSpanProcessor.
    const owned = state.ownedProvider;
    if (
      !owned ||
      (telemetry.tracer.getProvider() !== owned.tracerProvider &&
        !(telemetry.tracer.getProvider() instanceof ProxyTracerProvider))
    )
      return;
    telemetry.setTracerProvider(owned.tracerProvider, {
      registerSpanProcessor: owned.registerSpanProcessor,
    });
  });
}
