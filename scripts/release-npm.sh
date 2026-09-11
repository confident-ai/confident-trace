#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [VERSION] [--dry-run]"
  echo "Omit VERSION to accept a suggested next version or enter your own."
  echo "Example: $0 0.1.0-alpha.2"
  echo "Checks npm for duplicates, updates the version, logs in through the browser, and publishes."
  echo "--dry-run updates the local version and checks packaging without login or upload."
}
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
version=""
preview=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) preview=true ;;
    -*) usage >&2; exit 1 ;;
    *) if [[ -n "$version" ]]; then usage >&2; exit 1; fi; version="$arg" ;;
  esac
done
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root/typescript"

selection="$(node "$root/scripts/release-version.cjs" "$version")"
version="${selection%% *}"
tag="${selection#* }"

npm version "$version" --no-git-tag-version --allow-same-version --ignore-scripts
# The package's prepack hook runs pnpm build during publishing, embedding this version.
if [[ "$preview" == true ]]; then
  npm publish --dry-run --tag "$tag" --registry=https://registry.npmjs.org/
else
  npm login --auth-type=web --registry=https://registry.npmjs.org/
  npm publish --tag "$tag" --registry=https://registry.npmjs.org/
fi
