import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

const metadata = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf8'),
);

export default defineConfig({
  define: { __CONFIDENT_TRACE_VERSION__: JSON.stringify(metadata.version) },
  resolve: {
    alias: {
      '@test': fileURLToPath(new URL('./tests', import.meta.url)),
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    include: ['tests/**/*.test.ts'],
    testTimeout: 10000,
    restoreMocks: true,
  },
});
