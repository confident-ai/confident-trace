"""Lazy registry for SDK wrappers and future native-OTel integrations."""

import importlib

from .._core import runtime

_MODULES = {
    name: f"{__package__}.{name}.instrumentation"
    for name in (
        "llamaindex",
        "agno",
        "smolagents",
        "crewai",
        "langchain",
        "langgraph",
        "openai_agents",
        "claude_agent_sdk",
        "pydantic_ai",
        "strands",
        "microsoft_agent_framework",
        "litellm",
        "openrouter",
        "portkey",
        "openai",
        "anthropic",
        "google_genai",
        "bedrock",
        "google_adk",
        "agentcore",
    )
}
# LangGraph shares the callback bridge; its optional context hook lives with it.
_MODULES["langgraph"] = _MODULES["langchain"]


def instrument(rt, names):
    undo = []
    for name in dict.fromkeys(names):
        module = _MODULES.get(name)
        if module is None:
            continue
        try:
            undo.extend(importlib.import_module(module).instrument(rt))
        except ImportError:
            continue
        except Exception:
            runtime.log.debug("Integration unavailable: %s", name)
    return undo
