"""Keep integration ownership and lazy loading explicit as the SDK grows."""

import ast
import importlib.util
import subprocess
import sys

from conftest import ROOT

SOURCE = ROOT / "python/src/confident_trace"
PROVIDERS = {"openai", "anthropic", "google_genai", "bedrock"}
SDK_ROOTS = {"openai", "anthropic", "google", "boto3", "botocore"}


def imports(path):
    package = ".".join(("confident_trace", *path.relative_to(SOURCE).parts[:-1]))
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            base = importlib.util.resolve_name(name, package)
            yield base
            yield from (f"{base}.{alias.name}" for alias in node.names)


def test_dependency_boundaries():
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(SOURCE).parts
        owner = relative[1] if relative[0] == "integrations" else None
        for imported in imports(path):
            parts = imported.split(".")
            provider = (
                parts[2]
                if len(parts) > 2 and parts[:2] == ["confident_trace", "integrations"]
                else None
            )
            if relative[0] == "_core" or owner == "_shared":
                assert provider not in PROVIDERS, (path, imported)
                assert parts[0] not in SDK_ROOTS, (path, imported)
            elif owner in PROVIDERS:
                assert provider not in PROVIDERS - {owner}, (path, imported)


def test_import_and_registry_are_lazy():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import sys
from importlib.abc import MetaPathFinder
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

class MissingSDKs(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'openai', 'anthropic', 'google', 'boto3', 'botocore'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, MissingSDKs())
import confident_trace as ct
prefix = 'confident_trace.integrations.'
providers = {'openai', 'anthropic', 'google_genai', 'bedrock'}
def loaded():
    return {name[len(prefix):].split('.')[0] for name in sys.modules if name.startswith(prefix)} & providers
assert loaded() == set()
ct.init(tracer_provider=TracerProvider(), exporter=InMemorySpanExporter(), instrumentations=('openai',))
assert loaded() == {'openai'}
ct.shutdown()
ct.init(tracer_provider=TracerProvider(), exporter=InMemorySpanExporter())
assert loaded() == providers
ct.shutdown()
""",
        ],
        check=True,
    )


def test_registry_cleanup_is_idempotent_and_preserves_later_wrappers(telemetry):
    import openai.resources.chat.completions as sdk
    import wrapt

    from confident_trace._core import runtime
    from confident_trace.integrations import registry

    cls = sdk.Completions
    original = cls.create
    undo = registry.install(runtime.current(), ("openai", "openai"))
    ours = vars(cls)["create"]
    assert ours is not original
    assert registry.install(runtime.current(), ("openai",)) == []
    later = wrapt.FunctionWrapper(
        ours, lambda wrapped, instance, args, kwargs: wrapped(*args, **kwargs)
    )
    cls.create = later
    try:
        for restore in reversed(undo):
            restore()
        assert vars(cls)["create"] is later
        cls.create = ours
        for restore in reversed(undo):
            restore()
        assert cls.create is original
    finally:
        cls.create = original
        for restore in reversed(undo):
            restore()
