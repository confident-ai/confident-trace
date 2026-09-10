import { instrument } from '@/integrations/instrument';
import { enabled } from '@/auto/control';
import { patched, automaticOriginals } from '@/runtime/state';
import type { InstrumentationName } from '@/auto/types';
import {
  attempt,
  observed,
  replace,
  wrapConstructor,
  setExport,
} from '@/auto/patch';
import type { Foreign } from '@/auto/patch';

function methods(
  target: Foreign,
  names: string[],
  name: InstrumentationName,
): void {
  for (const key of names) {
    if (typeof target?.[key] !== 'function')
      throw new Error('Unsupported SDK resource');
    if (patched.has(target?.[key])) continue;
    replace(target, key, (original) => {
      const holder = { call: original };
      instrument(
        [[holder, 'call']],
        name === 'google-genai'
          ? 'google_genai'
          : (name as 'openai' | 'anthropic' | 'openrouter' | 'portkey'),
      );
      const wrapped = function (this: Foreign, ...args: Foreign[]) {
        return (enabled(name) ? holder.call : original).apply(this, args);
      };
      patched.add(wrapped);
      automaticOriginals.set(wrapped, original);
      return wrapped;
    });
  }
}
export function attachProvider(
  exports: Foreign,
  name: InstrumentationName,
): void {
  const className =
    name === 'openai'
      ? 'OpenAI'
      : name === 'portkey'
        ? 'Portkey'
        : name === 'openrouter'
          ? 'OpenRouter'
          : name === 'anthropic'
            ? 'Anthropic'
            : 'GoogleGenAI';
  for (const key of [className, 'default']) {
    if (typeof exports[key] !== 'function') continue;
    // Only SDK client constructors, not arbitrary default exports from resource modules.
    if (exports[key].name !== className) continue;
    setExport(
      exports,
      key,
      wrapConstructor(exports[key], (client) =>
        attempt(name, () => {
          if (name === 'openai' || name === 'portkey') {
            methods(client.chat?.completions, ['create'], name);
            methods(client.responses, ['create'], name);
          } else if (name === 'openrouter')
            methods(client.chat, ['send'], name);
          else if (name === 'anthropic')
            methods(client.messages, ['create', 'stream'], name);
          else
            methods(
              client.models,
              ['generateContent', 'generateContentStream'],
              name,
            );
        }),
      ),
    );
    observed(name);
  }
  // Prototype patches also cover SDK-internal and subclass construction.
  if (name === 'openai') {
    if (exports.Completions) {
      methods(exports.Completions.prototype, ['create'], name);
      observed(name);
    }
    if (exports.Responses) {
      methods(exports.Responses.prototype, ['create'], name);
      observed(name);
    }
  } else if (name === 'openrouter' && exports.Chat) {
    methods(exports.Chat.prototype, ['send'], name);
    observed(name);
  } else if (name === 'anthropic' && exports.Messages) {
    methods(exports.Messages.prototype, ['create', 'stream'], name);
    observed(name);
  }
}
