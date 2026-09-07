// Run the minimum OTel suite in a disposable checkout, leaving this checkout intact.
import { cp, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const repository = fileURLToPath(new URL('../../', import.meta.url));
const prepareOnly = process.argv.includes('--prepare-only');
const temporary = prepareOnly
  ? undefined
  : await mkdtemp(join(tmpdir(), 'confident-trace-minimum-'));
const destination = prepareOnly
  ? process.env.CI_MINIMUM_DESTINATION
  : join(temporary, 'repo');
if (!destination)
  throw new Error('CI_MINIMUM_DESTINATION is required with --prepare-only');
const excluded = new Set([
  'node_modules',
  'ci-results',
  '.pnpm-store',
  '.git',
  '.venv',
  'dist',
  '.ruff_cache',
  '.pytest_cache',
  '__pycache__',
]);
try {
  await cp(repository, destination, {
    recursive: true,
    filter: (path) =>
      !excluded.has(basename(path)) && !basename(path).startsWith('.venv'),
  });
  const packagePath = join(destination, 'typescript/package.json');
  const metadata = JSON.parse(await readFile(packagePath, 'utf8'));
  for (const key of Object.keys(metadata.dependencies)) {
    if (key.startsWith('@opentelemetry/'))
      metadata.dependencies[key] = key.includes('exporter-')
        ? '0.200.0'
        : '2.0.0';
  }
  for (const name of Object.keys(metadata.devDependencies)) {
    metadata.devDependencies[name] = JSON.parse(
      await readFile(
        join(repository, 'typescript/node_modules', name, 'package.json'),
        'utf8',
      ),
    ).version;
  }
  metadata.devDependencies['@opentelemetry/api'] = '1.9.0';
  await writeFile(packagePath, JSON.stringify(metadata, null, 2) + '\n');
  const run = (args) =>
    execFileSync('pnpm', args, {
      cwd: join(destination, 'typescript'),
      stdio: 'inherit',
    });
  run(['install', '--no-frozen-lockfile']);
  run(['exec', 'prettier', '--write', 'package.json']);
  if (!prepareOnly) run(['check']);
  console.log(
    prepareOnly
      ? 'Minimum OTel environment prepared'
      : 'Minimum OTel dependency suite passed in an isolated checkout',
  );
} finally {
  if (temporary) await rm(temporary, { recursive: true, force: true });
}
