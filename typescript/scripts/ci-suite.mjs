// A grouping layer only: existing tests and their assertions stay intact.
import { spawnSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const groups = JSON.parse(
  readFileSync(new URL('./ci-groups.json', import.meta.url), 'utf8'),
);
export function owner(file, name) {
  if (/^tests\/(unit|runtime|contract)\//.test(file)) return 'core';
  if (
    /^tests\/integrations\/(openrouter|portkey|bifrost|truefoundry)\.test\.ts$/.test(
      file,
    )
  )
    return 'openai';
  if (file === 'tests/integrations/langchain.test.ts') return 'langchain';
  if (file === 'tests/integrations/openai-agents.test.ts')
    return 'openai-agents';
  const key = name.startsWith('uses real AI SDK generation with ')
    ? 'uses real AI SDK generation with <integration> and preserves async parentage'
    : name;
  const suite = groups[file]?.[key];
  if (!suite) throw new Error(`Unassigned test: ${file}: ${name}`);
  return suite;
}

function run(args, capture = false) {
  const result = spawnSync('pnpm', args, {
    stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
    encoding: 'utf8',
    maxBuffer: 16 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0)
    throw new Error(
      `pnpm ${args.slice(0, 3).join(' ')} exited ${result.status}`,
    );
  return result.stdout;
}
const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function namePattern(names) {
  return `^(?:${[
    ...new Set(
      names.map((name) =>
        name.startsWith('uses real AI SDK generation with ')
          ? 'uses real AI SDK generation with [\\s\\S]+ and preserves async parentage'
          : escape(name.replaceAll(' > ', ' ')),
      ),
    ),
  ].join('|')})$`;
}

export function verifyResults(selected, report) {
  const executed = report.testResults.flatMap((file) =>
    file.assertionResults
      .filter((test) => !['pending', 'skipped', 'todo'].includes(test.status))
      .map(
        (test) =>
          `${relative(process.cwd(), file.name)}::${[...test.ancestorTitles, test.title].join(' > ')}`,
      ),
  );
  const wanted = selected.map((test) => `${test.file}::${test.name}`);
  const missing = wanted.filter((id) => !executed.includes(id));
  const extra = executed.filter((id) => !wanted.includes(id));
  if (missing.length || extra.length || !report.success)
    throw new Error(
      `Suite result mismatch: ${missing.length} missing, ${extra.length} extra; success=${report.success}`,
    );
}

function main() {
  const [suite, outputArg] = process.argv.slice(2);
  const output = resolve(
    outputArg || '../ci-results/typescript',
    suite || 'unknown',
  );
  mkdirSync(output, { recursive: true });
  if (suite === 'quality') {
    for (const task of [
      'lint',
      'format:check',
      'typecheck',
      'build',
      'test:packaging',
    ])
      run([task]);
    return;
  }
  const collected = JSON.parse(
    run(['exec', 'vitest', 'list', '--json'], true),
  ).map((test) => {
    const file = relative(process.cwd(), test.file);
    return { file, name: test.name, suite: owner(file, test.name) };
  });
  const selected = collected.filter((test) => test.suite === suite);
  writeFileSync(
    join(output, 'inventory.json'),
    JSON.stringify({ collected, selected }, null, 2) + '\n',
  );
  if (!selected.length) throw new Error(`Suite ${suite} selected no tests`);
  const names = [...new Set(selected.map((test) => test.name))];
  // Exact names keep shared files' hooks and fixture isolation unchanged.
  const pattern = namePattern(names);
  const report = join(output, 'results.json');
  run([
    'exec',
    'vitest',
    'run',
    ...new Set(selected.map((test) => test.file)),
    '--testNamePattern',
    pattern,
    '--reporter=default',
    '--reporter=json',
    '--reporter=junit',
    `--outputFile.json=${report}`,
    `--outputFile.junit=${join(output, 'junit.xml')}`,
  ]);
  verifyResults(selected, JSON.parse(readFileSync(report, 'utf8')));
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
)
  main();
