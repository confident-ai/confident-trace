import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import {
  cp,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  rm,
  writeFile,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), 'confident-trace-package-'));
const run = (command, args, cwd = temporary) =>
  execFileSync(command, args, {
    cwd,
    stdio: 'pipe',
    env: {
      ...process.env,
      npm_config_update_notifier: 'false',
      ...(command === 'npm'
        ? { npm_config_cache: join(temporary, 'npm-cache') }
        : {}),
    },
  });
try {
  const [packed] = JSON.parse(
    run(
      'npm',
      ['pack', '--ignore-scripts', '--json', '--pack-destination', temporary],
      root,
    ).toString(),
  );
  const archive = join(temporary, packed.filename);
  const packageFiles = packed.files.map((file) => file.path);
  assert(packageFiles.includes('LICENSE'));
  assert(
    packageFiles.every(
      (path) =>
        ['package.json', 'README.md', 'LICENSE'].includes(path) ||
        path.startsWith('dist/'),
    ),
  );
  const metadata = JSON.parse(
    await readFile(join(root, 'package.json'), 'utf8'),
  );
  const dependencies = { 'confident-trace': `file:${archive}` };
  for (const name of [
    ...Object.keys(metadata.dependencies),
    '@opentelemetry/api',
    '@types/node',
    'typescript',
  ]) {
    dependencies[name] = JSON.parse(
      await readFile(join(root, 'node_modules', name, 'package.json'), 'utf8'),
    ).version;
  }
  await writeFile(
    join(temporary, 'package.json'),
    JSON.stringify({ private: true, type: 'module', dependencies }),
  );
  run('pnpm', [
    'install',
    '--prefer-offline',
    '--ignore-scripts',
    '--config.confirmModulesPurge=false',
  ]);
  const installed = join(temporary, 'node_modules/confident-trace');
  assert.equal(
    await readFile(join(installed, 'LICENSE'), 'utf8'),
    await readFile(join(root, 'LICENSE'), 'utf8'),
  );
  for (const file of await readdir(join(installed, 'dist'))) {
    if (/\.(?:cjs|mjs|cts|mts)$/.test(file)) {
      const contents = await readFile(join(installed, 'dist', file), 'utf8');
      assert(!/["']@\//.test(contents), `Unresolved alias in ${file}`);
      assert(
        !contents.includes('__CONFIDENT_TRACE_VERSION__'),
        `Unresolved version in ${file}`,
      );
    }
  }
  await cp(
    join(root, 'tests/packaging/consumer.mjs'),
    join(temporary, 'consumer.mjs'),
  );
  process.stdout.write(
    execFileSync(process.execPath, ['consumer.mjs'], {
      cwd: temporary,
      env: { ...process.env, EXPECTED_PACKAGE_VERSION: metadata.version },
    }),
  );
  const source = await readFile(join(root, 'tests/types/consumer.mts'), 'utf8');
  await writeFile(join(temporary, 'consumer.mts'), source);
  await writeFile(join(temporary, 'consumer.cts'), source);
  for (const mode of ['NodeNext', 'Bundler']) {
    const config = {
      compilerOptions: {
        target: 'ES2022',
        module: mode === 'Bundler' ? 'ESNext' : 'NodeNext',
        moduleResolution: mode,
        strict: true,
        exactOptionalPropertyTypes: true,
        noEmit: true,
        skipLibCheck: false,
      },
      files:
        mode === 'Bundler'
          ? ['consumer.mts']
          : ['consumer.mts', 'consumer.cts'],
    };
    await writeFile(join(temporary, 'tsconfig.json'), JSON.stringify(config));
    run(process.execPath, [
      join(temporary, 'node_modules/typescript/bin/tsc'),
      '--project',
      'tsconfig.json',
    ]);
  }
  console.log(
    'Packed licenses, exports, aliases, and NodeNext/Bundler declarations passed',
  );
  await mkdir(join(temporary, 'scripts'), { recursive: true });

  // Exercise the actual tarball, not the workspace self-reference, with all SDKs.
  for (const name of [
    'openai',
    '@anthropic-ai/sdk',
    '@google/genai',
    '@langchain/core',
    '@langchain/langgraph',
    '@openai/agents',
    '@mastra/core',
    'ai',
    'esbuild',
    'tsx',
    'protobufjs',
    '@langchain/openai',
    '@ai-sdk/openai',
  ]) {
    dependencies[name] = JSON.parse(
      await readFile(join(root, 'node_modules', name, 'package.json'), 'utf8'),
    ).version;
  }
  await writeFile(
    join(temporary, 'package.json'),
    JSON.stringify({ private: true, type: 'module', dependencies }),
  );
  run('pnpm', [
    'install',
    '--prefer-offline',
    '--ignore-scripts',
    '--config.confirmModulesPurge=false',
  ]);
  for (const directory of [
    'tests/auto',
    'tests/support/proto',
    'examples/auto',
  ]) {
    await cp(join(root, directory), join(temporary, directory), {
      recursive: true,
    });
  }
  for (const script of ['test-auto.mjs', 'test-auto-examples.mjs']) {
    await cp(join(root, 'scripts', script), join(temporary, 'scripts', script));
    process.stdout.write(
      run(process.execPath, [join(temporary, 'scripts', script)]),
    );
  }
} catch (error) {
  if (error.stdout) process.stderr.write(error.stdout);
  if (error.stderr) process.stderr.write(error.stderr);
  throw error;
} finally {
  await rm(temporary, { recursive: true, force: true });
}
