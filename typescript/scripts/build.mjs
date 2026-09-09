import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { generateDtsBundle } from 'dts-bundle-generator';

const root = fileURLToPath(new URL('../', import.meta.url));
const metadata = JSON.parse(
  await readFile(new URL('../package.json', import.meta.url), 'utf8'),
);
await rm(new URL('../dist', import.meta.url), { recursive: true, force: true });
await mkdir(new URL('../dist', import.meta.url), { recursive: true });
const entries = {
  index: 'src/index.ts',
  otel: 'src/otel.ts',
  semconv: 'src/semconv/index.ts',
  state: 'src/runtime/state.ts',
  openai: 'src/integrations/openai/index.ts',
  anthropic: 'src/integrations/anthropic/index.ts',
  'google-genai': 'src/integrations/google-genai/index.ts',
  'vercel-ai': 'src/integrations/vercel-ai/index.ts',
  mastra: 'src/integrations/mastra/index.ts',
  langchain: 'src/integrations/langchain/index.ts',
  langgraph: 'src/integrations/langgraph/index.ts',
  'openai-agents': 'src/integrations/openai-agents/index.ts',
};
await build({
  absWorkingDir: root,
  plugins: [
    {
      name: 'shared-runtime-state',
      setup(build) {
        build.onResolve({ filter: /^@\/runtime\/state$/ }, () => ({
          path: './state.cjs',
          external: true,
        }));
      },
    },
  ],
  entryPoints: entries,
  outdir: 'dist',
  outExtension: { '.js': '.cjs' },
  bundle: true,
  packages: 'external',
  platform: 'node',
  target: 'node22',
  format: 'cjs',
  sourcemap: true,
  tsconfig: 'tsconfig.build.json',
  define: { __CONFIDENT_TRACE_VERSION__: JSON.stringify(metadata.version) },
});
const require = createRequire(import.meta.url);
for (const [name, entry] of Object.entries(entries)) {
  const exports = Object.keys(
    require(new URL(`../dist/${name}.cjs`, import.meta.url).pathname),
  );
  await writeFile(
    new URL(`../dist/${name}.mjs`, import.meta.url),
    `import implementation from './${name}.cjs';\n` +
      exports
        .map((key) => `export const ${key} = implementation.${key};`)
        .join('\n') +
      '\n',
  );
  const [declarations] = generateDtsBundle(
    [
      {
        filePath: `${root}${entry}`,
        output: { noBanner: true, exportReferencedTypes: false },
      },
    ],
    { preferredConfigPath: `${root}tsconfig.build.json` },
  );
  for (const extension of ['d.mts', 'd.cts']) {
    await writeFile(
      new URL(`../dist/${name}.${extension}`, import.meta.url),
      declarations,
    );
  }
}

// The preload installs hooks when evaluated: never execute it for export discovery.
await build({
  absWorkingDir: root,
  entryPoints: ['src/auto/register.ts'],
  outfile: 'dist/register.mjs',
  bundle: true,
  packages: 'external',
  platform: 'node',
  target: 'node22',
  format: 'esm',
  sourcemap: true,
  tsconfig: 'tsconfig.build.json',
  define: { __CONFIDENT_TRACE_VERSION__: JSON.stringify(metadata.version) },
  plugins: [
    {
      name: 'preload-shared-state',
      setup(build) {
        build.onResolve({ filter: /^@\/runtime\/state$/ }, () => ({
          path: './state.cjs',
          external: true,
        }));
      },
    },
  ],
});
