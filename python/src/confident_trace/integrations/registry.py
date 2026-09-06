"""Lazy registry for SDK wrappers and future native-OTel integrations."""

import importlib

from .._core import runtime

_MODULES = {
    name: f"{__package__}.{name}.instrumentation"
    for name in (
        "openai",
        "anthropic",
        "google_genai",
        "bedrock",
        "google_adk",
        "agentcore",
    )
}


def install(rt, names):
    undo = []
    for name in dict.fromkeys(names):
        module = _MODULES.get(name)
        if module is None:
            continue
        try:
            undo.extend(importlib.import_module(module).install(rt))
        except ImportError:
            continue
        except Exception:
            runtime.log.debug("Integration unavailable: %s", name)
    return undo
