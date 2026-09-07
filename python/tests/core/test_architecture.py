"""Keep integration ownership and lazy loading explicit as the SDK grows."""

import ast
import importlib.util
import subprocess
import sys

from conftest import ROOT

SOURCE = ROOT / "python/src/confident_trace"
PROVIDERS = {
    "crewai",
    "langchain",
    "openai",
    "anthropic",
    "google_genai",
    "bedrock",
    "google_adk",
    "agentcore",
    "microsoft_agent_framework",
    "pydantic_ai",
    "strands",
    "openai_agents",
    "claude_agent_sdk",
}
SDK_ROOTS = {
    "crewai",
    "langchain",
    "langchain_core",
    "langgraph",
    "agents",
    "openinference",
    "openai",
    "anthropic",
    "google",
    "boto3",
    "botocore",
    "bedrock_agentcore",
    "agent_framework",
    "pydantic_ai",
    "strands",
    "openai_agents",
    "claude_agent_sdk",
}


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
        if fullname.split('.')[0] in {'crewai', 'langchain', 'langchain_core', 'langgraph', 'openai', 'anthropic', 'google', 'boto3', 'botocore', 'bedrock_agentcore', 'agent_framework', 'pydantic_ai', 'strands', 'agents', 'claude_agent_sdk', 'openinference'}:
            raise ModuleNotFoundError(fullname)

sys.meta_path.insert(0, MissingSDKs())
import confident_trace as ct
prefix = 'confident_trace.integrations.'
providers = {'crewai', 'langchain', 'openai', 'anthropic', 'google_genai', 'bedrock', 'google_adk', 'agentcore', 'microsoft_agent_framework', 'pydantic_ai', 'strands', 'openai_agents', 'claude_agent_sdk'}
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
    undo = registry.instrument(runtime.current(), ("openai", "openai"))
    ours = vars(cls)["create"]
    assert ours is not original
    assert registry.instrument(runtime.current(), ("openai",)) == []
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


# Definitions are deliberately separated by ownership, not one union enum.
TELEMETRY_DEFINITIONS = {
    "_attributes.py",
    "_semconv/native.py",
    "integrations/google_adk/_constants.py",
    "integrations/microsoft_agent_framework/_constants.py",
    "integrations/pydantic_ai/_constants.py",
    "integrations/strands/_constants.py",
    "integrations/openai_agents/_constants.py",
    "integrations/claude_agent_sdk/_constants.py",
}
TELEMETRY_PREFIXES = (
    "gen_ai.",
    "confident.",
    "confident_trace.",
    "gcp.",
    "aws.",
    "azure.",
    "server.",
    "service.",
    "session.",
    "cloud.",
    "http.",
    "url.",
    "network.",
    "client.",
    "db.",
    "rpc.",
    "messaging.",
    "error.",
    "user.",
    "code.",
    "process.",
    "otel.",
    "telemetry.",
    "openai.",
    "OTEL_",
)


def telemetry_literals(source):
    tree = ast.parse(source)
    docs = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
        ):
            # SDK import targets are Python module paths, not wire attributes.
            if node.value.startswith("openai.resources."):
                continue
            if (
                node.value.startswith(TELEMETRY_PREFIXES)
                or node.value == "confident_trace"
            ):
                yield node.lineno, node.value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {
                "set_attribute",
                "add_event",
                "get_tracer",
                "create_key",
            }:
                first = (
                    node.args[0]
                    if node.args
                    else next(
                        (
                            k.value
                            for k in node.keywords
                            if k.arg in {"key", "name", "instrumenting_module_name"}
                        ),
                        None,
                    )
                )
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    yield first.lineno, first.value


def test_runtime_telemetry_literals_are_centralized():
    for path in SOURCE.rglob("*.py"):
        name = path.relative_to(SOURCE).as_posix()
        if name in TELEMETRY_DEFINITIONS or (
            name.startswith("_semconv/genai_v") and name.endswith(".py")
        ):
            continue
        assert not list(telemetry_literals(path.read_text())), name


def test_literal_guard_catches_attributes_scopes_and_dynamic_prefixes():
    assert list(telemetry_literals('span.set_attribute("new.namespace", value)'))
    assert list(telemetry_literals('key = "confident.trace." + field'))
    assert list(telemetry_literals('key = f"gen_ai.{field}"'))
    assert list(telemetry_literals('provider.get_tracer("new-library")'))
    assert list(telemetry_literals('os.getenv("OTEL_SDK_DISABLED")'))
    assert not list(telemetry_literals("span.set_attribute(attrs.TRACE_INPUT, value)"))
    assert not list(
        telemetry_literals('"""gen_ai.operation.name is documented here."""')
    )


def test_native_recognition_does_not_depend_on_emission_schema():
    path = SOURCE / "_semconv/native.py"
    assert not any("genai_v" in imported for imported in imports(path))
