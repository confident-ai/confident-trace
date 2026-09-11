import { execFileSync } from 'node:child_process';
import { mkdtemp, rm, writeFile, readFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(root, '.auto-tests-'));
function run(file, args = [], env = {}, timeout = 60000) {
  process.stdout.write(
    execFileSync(process.execPath, [...args, file], {
      cwd: root,
      env: { ...process.env, ...env },
      timeout,
      encoding: 'utf8',
    }),
  );
}
try {
  const preload = ['--import', 'confident-trace/register'];
  run('tests/auto/truefoundry.mjs', preload);
  run('tests/auto/truefoundry.cjs', preload);
  run('tests/auto/bifrost.mjs', preload);
  run('tests/auto/bifrost.cjs', preload);
  run('tests/auto/portkey.cjs', preload);
  run('tests/auto/openrouter.cjs', preload);
  run('tests/auto/smoke.mjs', preload);
  run('tests/auto/dynamic.mjs', preload);
  run('tests/auto/frameworks.mjs', preload);
  run('scripts/test-framework-loading.mjs', [], {}, 300000);
  run('tests/auto/smoke.mjs', preload, { AUTO_PRIVATE: 'true' });
  const source = await readFile(join(root, 'tests/auto/smoke.mjs'), 'utf8');
  await build({
    stdin: {
      contents:
        source.replace(
          'const sink =',
          'async function main() {\nconst sink =',
        ) +
        '\n}\nmain().catch(error => { console.error(error); process.exitCode = 1; });',
      resolveDir: root,
    },
    outfile: join(temporary, 'smoke.cjs'),
    bundle: true,
    packages: 'external',
    external: ['confident-trace', 'confident-trace/*'],
    platform: 'node',
    format: 'cjs',
  });
  run(join(temporary, 'smoke.cjs'), preload);
  for (const mode of [
    'normal',
    'private',
    'redact',
    'manual',
    'missing',
    'disabled',
  ]) {
    run(
      'tests/auto/lifecycle.mjs',
      mode === 'missing' ? [] : [...preload, ...preload],
      {
        AUTO_MODE: mode,
        OTEL_SDK_DISABLED: mode === 'disabled' ? 'true' : 'false',
      },
    );
  }
  await writeFile(
    join(temporary, 'entry.ts'),
    `import { init } from 'confident-trace';\nconst runtime = init({ instrumentations: [] });\nif (!runtime.getInstrumentationStatus().hookRegistered) throw new Error('preload missing');\nawait runtime.shutdown();\n`,
  );
  run(join(temporary, 'entry.ts'), ['--import', 'tsx', ...preload]);
  for (const [name, version, selection, expected] of [
    ['unsupported', '99.0.0', 'all', 'unsupported'],
    ['failed', '7.10.0', 'all', 'failed'],
    ['opt-out', '99.0.0', [], 'disabled'],
  ]) {
    const fixture = join(temporary, name);
    await mkdir(join(fixture, 'node_modules/openai'), { recursive: true });
    await writeFile(
      join(fixture, 'node_modules/openai/package.json'),
      JSON.stringify({
        name: 'openai',
        version,
        type: 'module',
        exports: './index.js',
      }),
    );
    await writeFile(
      join(fixture, 'node_modules/openai/index.js'),
      'export default class OpenAI {}',
    );
    await writeFile(
      join(fixture, 'entry.mjs'),
      `import assert from 'node:assert/strict';
import { init } from 'confident-trace';
import OpenAI from 'openai';
const warnings = []; console.warn = value => warnings.push(value);
new OpenAI();
const rt = init({ instrumentations: ${JSON.stringify(selection)} });
assert.equal(rt.getInstrumentationStatus().integrations.openai, ${JSON.stringify(expected)});
assert.equal(warnings.length, ${expected === 'disabled' ? 0 : 1});
await rt.shutdown();
`,
    );
    run(join(fixture, 'entry.mjs'), preload);
  }
  console.log('Automatic instrumentation startup suite passed');
} finally {
  await rm(temporary, { recursive: true, force: true });
}
