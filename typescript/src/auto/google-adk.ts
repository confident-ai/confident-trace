import { onActivate } from '@/auto/control';
import { observed } from '@/auto/patch';
import { enableSpanContent, SCOPE } from '@/integrations/google-adk';
import { state } from '@/runtime/state';
import { INTEGRATIONS } from '@/semconv/generated';

let attached = false;
let restore: (() => void) | undefined;

/**
 * ADK emits native OTel spans on the global provider, so nothing is patched:
 * label its scope, let provider wrappers defer to its inference spans, and
 * turn its span content on. Called once per loaded ADK module file.
 */
export function attachGoogleAdk(): void {
  if (attached) return;
  attached = true;
  observed('google-adk');
  onActivate('google-adk', () => {
    state.integrationScopes.set(SCOPE, INTEGRATIONS.google_adk);
    if (restore) return;
    restore = enableSpanContent();
    if (restore)
      state.auto.owned.add({
        close: () => {
          restore?.();
          restore = undefined;
        },
      });
  });
}
