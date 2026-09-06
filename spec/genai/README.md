# Versioned GenAI registry

The JSON filename is the upstream semantic-convention version, not the SDK version.
The registry catalogs the full pinned GenAI vocabulary, including deprecated
attributes/events, provider definitions, inherited requirements, metrics, message
schemas, and normative documentation. It does not claim that we emit every signal.

## Provenance and licensing

Version 1.37.0 comes from OpenTelemetry semantic-conventions tag `v1.37.0`, commit
`aec6e9d3e86754683dab7c707655d69d953b2768`. The source is Apache-2.0 licensed. The
snapshot includes the original license, unmodified source texts, per-file SHA-256,
and a source-set checksum. The resolved definitions are generated from those texts.

## Updating conventions

1. Fetch and verify an upstream release tag and its full commit hash. Never use
   moving `main` as a release identifier. Preserve the actual upstream schema URL;
   a future separately versioned GenAI release may require updating the importer.
2. Run `python tools/import_genai.py UPSTREAM_CHECKOUT --version VERSION --commit SHA`
   with PyYAML installed. The importer refuses to replace a differing snapshot.
3. Review upstream changes, implement the chosen emission changes, and update
   shared vectors. Add a release manifest recording the new registry byte hash,
   SDK coverage, supported dependencies, and deviations. Record changes in the changelog.
4. Update the active release manifest path in the generator, then run
   `python tools/generate_genai.py`. Generated constants and compatibility docs
   are checked in; the SDK performs no registry reads or network requests at runtime.
5. Run tests and `python tools/generate_genai.py --check`. CI also compares existing
   snapshots against the base Git revision, so snapshots cannot be silently changed.

Each released language/version gets its own release manifest and coverage matrix;
all languages share convention snapshots. Python constants are generated today.
TypeScript remains empty until its implementation starts. Known enum constants
are suggestions, never a closed allowlist for telemetry.
