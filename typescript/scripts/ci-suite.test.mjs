import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { owner, verifyResults, namePattern } from './ci-suite.mjs';

test('shared tests keep one explicit owner', () => {
  assert.equal(
    owner(
      'tests/integrations/providers.test.ts',
      'captures Anthropic tool fragments and Google complete tool calls',
    ),
    'core',
  );
  assert.equal(
    owner(
      'tests/integrations/frameworks.test.ts',
      'respects disabled tracing in both framework integrations',
    ),
    'core',
  );
  assert.equal(
    owner('tests/integrations/langchain.test.ts', 'a graph test'),
    'langchain',
  );
  assert.throws(
    () => owner('tests/integrations/providers.test.ts', 'new unmapped test'),
    /Unassigned/,
  );
});

test('missing, skipped, extra and failed test results cannot pass', () => {
  const selected = [{ file: 'tests/example.test.ts', name: 'runs' }];
  const report = (assertions, success = true) => ({
    success,
    testResults: [
      { name: resolve('tests/example.test.ts'), assertionResults: assertions },
    ],
  });
  verifyResults(
    selected,
    report([
      { fullName: 'runs', title: 'runs', ancestorTitles: [], status: 'passed' },
    ]),
  );
  assert.throws(() => verifyResults(selected, report([])), /missing/);
  assert.throws(
    () =>
      verifyResults(
        selected,
        report([
          {
            fullName: 'runs',
            title: 'runs',
            ancestorTitles: [],
            status: 'pending',
          },
        ]),
      ),
    /missing/,
  );
  assert.throws(
    () =>
      verifyResults(
        selected,
        report([
          {
            fullName: 'other',
            title: 'other',
            ancestorTitles: [],
            status: 'passed',
          },
        ]),
      ),
    /extra/,
  );
  assert.throws(
    () => verifyResults(selected, report([], false)),
    /success=false/,
  );
});

// Vitest's %s expands constructors into large multiline source strings.
test('name filter handles nested titles and large parameter values', () => {
  const large =
    'uses real AI SDK generation with class {\n' +
    'x'.repeat(200000) +
    '\n} and preserves async parentage';
  const pattern = namePattern([large, 'export configuration > defaults']);
  assert.ok(pattern.length < 200);
  assert.ok(new RegExp(pattern).test(large));
  assert.ok(new RegExp(pattern).test('export configuration defaults'));
  assert.ok(!new RegExp(pattern).test('unrelated'));
});
