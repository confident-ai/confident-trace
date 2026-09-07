import { mkdtemp, rm } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { build } from 'esbuild';

const temporary = await mkdtemp(join(tmpdir(), 'confident-trace-examples-'));
const require = createRequire(import.meta.url);
try {
  for (const name of ['basic', 'existing-provider']) {
    const outfile = join(temporary, `${name}.mjs`);
    await build({
      entryPoints: [`examples/${name}.ts`],
      outfile,
      bundle: true,
      packages: 'external',
      platform: 'node',
      format: 'esm',
      plugins: [
        {
          name: 'installed-dependencies',
          setup(builder) {
            builder.onResolve(
              { filter: /^@(?:opentelemetry|grpc)\// },
              (args) => ({ path: require.resolve(args.path), external: true }),
            );
          },
        },
      ],
      define: { __CONFIDENT_TRACE_VERSION__: '"example"' },
    });
    process.stdout.write(execFileSync(process.execPath, [outfile]));
  }
} finally {
  await rm(temporary, { recursive: true, force: true });
}
