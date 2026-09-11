import { execFileSync } from 'node:child_process';
import {
  mkdtemp,
  rm,
  writeFile,
  readFile,
  mkdir,
  cp,
  symlink,
} from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(root, '.auto-tests-'));
function run(file, args = [], env = {}, scriptArgs = []) {
  process.stdout.write(
    execFileSync(process.execPath, [...args, file, ...scriptArgs], {
      cwd: root,
      env: { ...process.env, ...env },
      timeout: 60000,
      encoding: 'utf8',
    }),
  );
}
try {
  const preload = ['--import', 'confident-trace/register'];
  for (const provider of [
    'openai',
    'anthropic',
    'openrouter',
    'portkey',
    'google',
  ])
    run('tests/auto/provider-results.mjs', preload, {}, [provider]);
  run('tests/auto/mastra-result.mjs', preload);
  run('tests/auto/framework-results.mjs', preload);
  run('tests/auto/langchain-result.mjs', preload);
  run('tests/auto/vercel-loading.mjs', preload);
  // A real second ai copy, with dependencies resolved from its original install.
  const require = createRequire(import.meta.url);
  const aiRoot = dirname(require.resolve('ai/package.json'));
  const isolated = join(temporary, 'separate-ai');
  await mkdir(join(isolated, 'node_modules'), { recursive: true });
  await cp(aiRoot, join(isolated, 'node_modules/ai'), { recursive: true });
  await symlink(
    dirname(aiRoot),
    join(isolated, 'node_modules/ai/node_modules'),
    'dir',
  );
  await cp(
    join(root, 'tests/auto/vercel-loading.mjs'),
    join(isolated, 'entry.mjs'),
  );
  run(join(isolated, 'entry.mjs'), preload);

  run('tests/auto/vercel-loading.mjs', preload, { BRIDGE_FIRST: 'true' });
  const loading = await readFile(
    join(root, 'tests/auto/vercel-loading.mjs'),
    'utf8',
  );
  await writeFile(join(temporary, 'vercel-loading.ts'), loading);
  run(join(temporary, 'vercel-loading.ts'), ['--import', 'tsx', ...preload]);
  // Exercise the same real framework assertions after init, in a fresh process.
  let dynamic = await readFile(join(root, 'tests/auto/smoke.mjs'), 'utf8');
  // Keep this matrix scoped to frameworks; provider transport mocks have their own suites.
  dynamic = dynamic.replace(
    /^import (?:OpenAI|Anthropic|\{ GoogleGenAI \}) from '[^']+';\n/gm,
    '',
  );
  const providerStart = dynamic.indexOf('const client =');
  const providerEnd = dynamic.indexOf('await RunnableLambda');
  dynamic =
    dynamic.slice(0, providerStart) +
    'const rt = init({ exporter: sink });\nconst mastra = new Mastra({ logger: false });\n' +
    dynamic.slice(providerEnd);
  dynamic = dynamic.replace(/ {2}'(?:OpenAI|Anthropic|Google GenAI)',\n/g, '');
  dynamic = dynamic.replace(
    'All eight automatic integrations passed',
    'All five framework integrations passed',
  );
  await writeFile(join(temporary, 'frameworks-static.mjs'), dynamic);
  run(join(temporary, 'frameworks-static.mjs'), preload);
  const imports = [];
  dynamic = dynamic.replace(
    /^import (.+) from '([^']+)';$/gm,
    (line, bindings, name) => {
      if (
        [
          'node:assert/strict',
          'confident-trace',
          '@opentelemetry/sdk-trace-base',
        ].includes(name)
      )
        return line;
      imports.push(
        bindings.startsWith('{')
          ? `const ${bindings} = await import('${name}');`
          : `const { default: ${bindings} } = await import('${name}');`,
      );
      return '';
    },
  );
  const setup = `const rt = init({ exporter: sink, captureContent: true });`;
  dynamic = dynamic.replace(
    'const rt = init({ exporter: sink });',
    setup + '\n' + imports.join('\n'),
  );
  await writeFile(join(temporary, 'frameworks-dynamic.mjs'), dynamic);
  run(join(temporary, 'frameworks-dynamic.mjs'), preload);

  console.log('Framework import-order regressions passed');
} finally {
  await rm(temporary, { recursive: true, force: true });
}
