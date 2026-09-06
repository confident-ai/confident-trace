"""Snapshot a tagged upstream checkout; never overwrite a different snapshot.

Usage: python tools/import_genai.py CHECKOUT --version 1.37.0 --commit SHA
Build-time dependency: PyYAML. Runtime SDK has no YAML dependency.
"""

import argparse
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def snapshot(upstream, version, commit):
    files = set(upstream.glob("model/gen-ai/**/*.yaml"))
    files.update(upstream.glob("model/openai/**/*.yaml"))
    definitions = {}
    locations = {}
    for path in sorted(upstream.glob("model/**/*.yaml")):
        for group in (yaml.safe_load(path.read_text()) or {}).get("groups", []):
            for attr in group.get("attributes", []):
                if "id" in attr:
                    definitions[attr["id"]] = attr
                    locations[attr["id"]] = path
    groups = {}
    for path in sorted(files):
        for group in yaml.safe_load(path.read_text()).get("groups", []):
            groups[group["id"]] = group
    # Carry every referenced attribute definition, including general OTel keys.
    for group in groups.values():
        for attr in group.get("attributes", []):
            if "ref" in attr:
                files.add(locations[attr["ref"]])
    attributes = {}
    for group in groups.values():
        for attr in group.get("attributes", []):
            key = attr.get("id", attr.get("ref"))
            attributes[key] = definitions[key]

    def resolve(key):
        group = groups[key]
        inherited = resolve(group["extends"]) if group.get("extends") else {}
        for attr in group.get("attributes", []):
            name = attr.get("id", attr.get("ref"))
            inherited[name] = {**definitions[name], **inherited.get(name, {}), **attr}
        return inherited

    resolved = {
        key: {**group, "attributes": resolve(key)} for key, group in groups.items()
    }
    docs = sorted(p for p in (upstream / "docs/gen-ai").glob("*") if p.is_file())
    files.update(docs)
    files.add(upstream / "LICENSE")
    sources = {}
    for path in sorted(files):
        raw = path.read_bytes()
        sources[path.relative_to(upstream).as_posix()] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "text": raw.decode(),
        }
    checksum = hashlib.sha256(
        json.dumps(
            {key: value["sha256"] for key, value in sources.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "format_version": 1,
        "version": version,
        "schema_url": f"https://opentelemetry.io/schemas/{version}",
        "upstream": {
            "repository": "https://github.com/open-telemetry/semantic-conventions",
            "tag": f"v{version}",
            "commit": commit,
            "license": "Apache-2.0",
            "source_checksum_sha256": checksum,
            "checksum_algorithm": "SHA256 of compact sorted JSON mapping source paths to SHA256 bytes",
        },
        "attributes": dict(sorted(attributes.items())),
        "groups": dict(sorted(resolved.items())),
        "message_schemas": {
            p.stem.removeprefix("gen-ai-"): json.loads(p.read_text())
            for p in docs
            if p.suffix == ".json"
        },
        "sources": sources,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if len(args.commit) != 40 or any(c not in "0123456789abcdef" for c in args.commit):
        parser.error("commit must be the verified full upstream commit hash")
    result = snapshot(args.checkout, args.version, args.commit)
    path = ROOT / "spec/genai" / f"{args.version}.json"
    data = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if path.exists() and path.read_text() != data:
        raise SystemExit("Refusing to replace an existing convention snapshot")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)


if __name__ == "__main__":
    main()
