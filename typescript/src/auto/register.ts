import { register, createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { addHook } from 'import-in-the-middle';
import { Hook } from 'require-in-the-middle';
import { satisfies } from 'semver';
import { state } from '@/runtime/state';
import { warn, failed } from '@/auto/control';
import { attachProvider } from '@/auto/providers';
import { attachLangChain, attachLangGraph } from '@/auto/langchain';
import { attachVercel } from '@/auto/vercel';
import { attachMastra } from '@/auto/mastra';
import { attachAgents } from '@/auto/agents';
import type { InstrumentationName } from '@/auto/types';
import type { Foreign } from '@/auto/patch';

const packages: Record<string, [InstrumentationName, string]> = {
  'portkey-ai': ['portkey', '>=3.1.0 <3.2'],
  '@openrouter/sdk': ['openrouter', '>=1.2.116 <1.3'],
  openai: ['openai', '>=7.10.0 <8'],
  '@anthropic-ai/sdk': ['anthropic', '>=0.124.0 <0.125'],
  '@google/genai': ['google-genai', '>=2.21.0 <3'],
  ai: ['vercel-ai', '>=7.0.93 <8'],
  '@langchain/core': ['langchain', '>=1.2.9 <2'],
  '@langchain/langgraph': ['langgraph', '>=1.4.14 <2'],
  '@mastra/core': ['mastra', '>=1.64.0 <2'],
  '@openai/agents': ['openai-agents', '>=0.17.0 <0.18'],
  '@openai/agents-core': ['openai-agents', '>=0.17.0 <0.18'],
  '@openai/agents-openai': ['openai-agents', '>=0.17.0 <0.18'],
};
const packageRequire = createRequire(import.meta.url);
const seen = new Map<string, boolean>();
function attach(exports: Foreign, packageName: string, base: string): void {
  const entry = packages[packageName];
  if (!entry) return;
  const [name, range] = entry;
  try {
    if (!seen.has(base)) {
      const { version } = JSON.parse(
        readFileSync(join(base, 'package.json'), 'utf8'),
      );
      seen.set(base, satisfies(version, range));
      if (!seen.get(base)) {
        if (
          !['enabled', 'failed'].includes(state.auto.observed.get(name) ?? '')
        )
          state.auto.observed.set(name, 'unsupported');
        warn(
          `unsupported:${base}`,
          `${name}: unsupported SDK version ${version}; supported range is ${range}. Use a supported version or manual tracing.`,
          name,
        );
      }
    }
    if (!seen.get(base)) return;
    if (
      ['openai', 'openrouter', 'portkey', 'anthropic', 'google-genai'].includes(
        name,
      )
    )
      attachProvider(exports, name);
    else if (name === 'langchain') attachLangChain(exports);
    else if (name === 'langgraph') attachLangGraph(exports);
    else if (name === 'vercel-ai')
      attachVercel(exports, packageRequire.resolve('@ai-sdk/otel'));
    else if (name === 'mastra')
      attachMastra(exports, packageRequire.resolve('@mastra/observability'));
    else attachAgents(exports);
  } catch {
    failed(name);
  }
}
if (!state.auto.registered) {
  const names = Object.keys(packages);
  const pattern =
    /\/node_modules\/(openai|ai|portkey-ai|@openrouter\/sdk|@anthropic-ai\/sdk|@google\/genai|@langchain\/(?:core|langgraph)|@mastra\/core|@openai\/agents(?:-core|-openai)?)(?=\/)/;
  addHook((url, exports) => {
    const path = fileURLToPath(url);
    const match = pattern.exec(path);
    if (!match) return;
    attach(exports, match[1]!, path.slice(0, match.index + match[0].length));
  });
  new Hook(names, { internals: true }, (exports, name, base) => {
    if (!base) return exports;
    const packageName = names.find(
      (n) => name === n || name.startsWith(`${n}/`),
    );
    if (!packageName) return exports;
    const descriptors = Object.getOwnPropertyDescriptors(exports);
    const namespace: Foreign =
      typeof exports === 'function'
        ? { default: exports }
        : Object.create(
            Object.getPrototypeOf(exports),
            Object.fromEntries(
              Object.entries(descriptors).map(([key, descriptor]) => [
                key,
                {
                  ...descriptor,
                  configurable: true,
                  ...('value' in descriptor ? { writable: true } : {}),
                },
              ]),
            ),
          );
    attach(namespace, packageName, base);
    if (typeof exports === 'function')
      return namespace.default as typeof exports;
    // Preserve identity and non-enumerable __esModule markers for untouched modules.
    const changed = Object.keys(namespace).some(
      (key) => namespace[key] !== (exports as Foreign)[key],
    );
    return (changed ? namespace : exports) as typeof exports;
  });
  register(packageRequire.resolve('import-in-the-middle/hook.mjs'), {
    parentURL: import.meta.url,
    data: { include: [pattern] },
  });
  state.auto.registered = true;
}
