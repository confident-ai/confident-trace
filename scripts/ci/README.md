# Grouped CI

GitHub shows 29 stable rows: 19 Python, 9 TypeScript, and Shared / Consistency.
Only suite IDs expand the Actions matrix. Runtime and dependency combinations
run sequentially within each row, using fresh environments. Separate rows run
in parallel and one row's failure does not cancel another.

## Reproduce a combination

```sh
# Python: install uv and the desired interpreter first.
uv python install 3.13
python3 scripts/ci/run.py python openai --runtime 3.13 --profile base --dependencies minimum
python3 scripts/ci/run.py python strands --runtime 3.13 --dependencies tested

# TypeScript: NODE_22 / NODE_24 are directories containing the corresponding node.
# They are optional if the requested major version is already on PATH.
NODE_24=/path/to/node24/bin python3 scripts/ci/run.py typescript anthropic --runtime 24 --dependencies current

# Show combinations without installing or running anything.
python3 scripts/ci/run.py python core --dry-run
```

Omit filters to run the complete row. Invalid suites or empty filters fail.
Python uses `uv python find` and standard venv/pip; TypeScript uses pnpm 10.14.0.
The workflows provision all required runtimes. No globally installed SDK is used.

## Coverage ownership

`suites.py` defines Python ownership and the original seven environments:
base (minimum/latest), native, microsoft, agent-sdks, langchain, crewai and
frameworks (tested/latest), on Python 3.10–3.13. Extras and constraints are
unchanged. The four provider rows and Core each retain all 56 combinations.
Each framework row retains its eight combinations in its home environment.

Every Python invocation collects the original full test directory before
selecting tests. `collection_plugin.py` records every collected node ID and its
owner, including all parameters. Unrecognized test families fail collection.
The partition preserves all original test/environment pairings:

- Core and contracts belong to Core in all environments.
- Provider tests belong to their provider row in all environments.
- Framework tests belong to their integration row in its original home environment.
- Tests of frameworks in *other* environments belong to Interoperability. This
  preserves incidental coverage through transitive dependencies without guessing
  which dependencies a future latest resolution will install.
- Shared native lifecycle and mixed SDK tests belong to Interoperability, which
  also retains the original Python 3.13 mega environment and offline command.

The original suite has missing-extra skips outside an integration's home
environment; these remain intentional. Home rows verify required distributions
before pytest and reject missing-dependency skips or an empty selection. Existing
xfails and deliberate platform/framework skips remain intact. SDK subprocess
isolation and scenario assertions are unchanged.

TypeScript retains Node 22/24 and current/minimum OTel dependencies. Each run
lists the original Vitest collection and assigns every case to exactly one row.
`typescript/scripts/ci-groups.json` explicitly groups mixed files by test name;
LangChain/LangGraph and OpenAI Agents own their existing files. Core includes
mixed-provider/framework cases. Test names, fixtures and assertions are untouched.
New tests in mixed files need an explicit owner; unmapped tests fail the audit.
Exact test-name filtering keeps existing file hooks and Vitest worker isolation.
The runner compares selected cases with executed report entries and rejects
missing, skipped or extra cases. Normal `pnpm test` still runs the entire suite.

Build & quality retains each language's original build/quality combinations.
Registry checks run on Python 3.10–3.13 and shared-file checks run on Node 22/24
in Shared / Consistency. These scripts use only standard-library dependencies. They also validate
the grouping infrastructure without adding another check row.

## Reports and failure behavior

`ci-results/<language>/<suite>/` contains a summary and `results.json`, plus
per-combination logs, collection inventories and test reports. Python records
resolved dependencies; TypeScript's lockfile and minimum preparation log record
its resolution. Installation, execution, collection and report-validation failures
fail the combination. Remaining combinations still run and the row fails at the
end if any combination failed. Actions always uploads available reports and
links the artifact from the job summary. An interrupted job cannot report success.

## Validation and rollout

```sh
python -m unittest discover -s scripts/ci -p 'test_*.py'
node --test typescript/scripts/ci-suite.test.mjs
```

These validate 29 workflow names, version/profile coverage, ownership,
unknown-test rejection, continuation after failure, collection reporting,
empty-selection failure, expected failures, and missing/skipped/extra TS results.
Runtime collection inventories provide the complete partition for each actual
dependency resolution; installation failures remain failures, never invented
passing inventories.

Keep the existing push and pull_request triggers. After the new checks have
appeared on GitHub, replace any required old matrix check names in repository
rulesets/branch protection with the new stable names. No ruleset change is made
by these workflows. No SDK public API or existing test assertion changes.
