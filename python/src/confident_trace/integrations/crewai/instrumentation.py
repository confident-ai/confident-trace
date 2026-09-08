"""Owned execution hooks inspired by OpenInference's wrapper-based coverage.

Model calls deliberately remain with the provider adapters. CrewAI LLM.call can
also execute tools; treating that whole method as inference misparents tools and
suppresses direct provider calls made by them.
"""

import importlib

import wrapt

from ..._core.safety import safe
from .execution import State, call_context, execution_wrapper

TARGETS = (
    (
        "crewai.experimental.agent_executor",
        "AgentExecutor",
        "_execute_single_native_tool_call",
        "call_context",
    ),
    ("crewai", "Crew", "kickoff", "crew"),
    ("crewai", "Crew", "akickoff", "crew"),
    ("crewai", "Task", "_execute_core", "task"),
    ("crewai", "Task", "_aexecute_core", "task"),
    ("crewai", "Agent", "execute_task", "agent"),
    ("crewai", "Agent", "aexecute_task", "agent"),
    ("crewai", "Agent", "_execute_and_build_output", "agent"),
    ("crewai", "Agent", "_execute_and_build_output_async", "agent"),
    ("crewai.flow.flow", "Flow", "kickoff_async", "flow"),
    ("crewai.flow.flow", "Flow", "resume_async", "flow"),
    ("crewai.flow.flow", "Flow", "_execute_method", "node"),
    ("crewai.tools.base_tool", "BaseTool", "run", "tool"),
    ("crewai.tools.base_tool", "BaseTool", "arun", "tool"),
    ("crewai.tools.base_tool", "Tool", "run", "tool"),
    ("crewai.tools.base_tool", "Tool", "arun", "tool"),
    ("crewai.tools.structured_tool", "CrewStructuredTool", "invoke", "tool"),
    ("crewai.tools.structured_tool", "CrewStructuredTool", "ainvoke", "tool"),
    (
        "crewai.agents.crew_agent_executor",
        "CrewAgentExecutor",
        "_execute_single_native_tool_call",
        "call_context",
    ),
)


def instrument(rt, *, include_llm=False):
    import crewai  # noqa: F401 — selected adapters alone import their framework.

    previous = getattr(rt, "_crewai_state", None)
    owned = previous is None or previous.closed
    if not owned and not include_llm:
        return []
    state = State(rt) if owned else previous
    rt._crewai_state = state
    undo = []
    targets = TARGETS + (
        (("crewai", "LLM", "call", "llm"), ("crewai", "LLM", "acall", "llm"))
        if include_llm
        else ()
    )
    for module, class_name, method, kind in targets:
        safe(patch, module, class_name, method, kind, state, undo)
    if include_llm:
        from crewai import LLM
        from crewai.llms.base_llm import BaseLLM

        from .._shared.execution import patch as patch_method

        def instrument_model(cls):
            for method in ("call", "acall"):
                original = getattr(cls, method, None)
                if original is not None:
                    patch_method(
                        cls, method, execution_wrapper(state, "llm", original), undo
                    )

        pending = list(BaseLLM.__subclasses__())
        seen = set()
        while pending:
            cls = pending.pop()
            if cls in seen:
                continue
            seen.add(cls)
            instrument_model(cls)
            pending.extend(cls.__subclasses__())

        def create(wrapped, instance, args, kwargs):
            result = wrapped(*args, **kwargs)
            if state.enabled():
                instrument_model(type(result))
            return result

        patch_method(LLM, "__new__", create, undo)
    return [*undo, state.close] if owned else undo


def patch(module, class_name, method, kind, state, undo):
    cls = getattr(importlib.import_module(module), class_name)
    original = getattr(cls, method, None)
    if original is None or isinstance(original, wrapt.ObjectProxy):
        return
    owned_attribute = method in vars(cls)
    wrapper = (
        call_context
        if kind == "call_context"
        else execution_wrapper(
            state,
            kind,
            original,
            structured_async=class_name == "CrewStructuredTool" and method == "ainvoke",
        )
    )
    patched = wrapt.FunctionWrapper(original, wrapper)
    setattr(cls, method, patched)

    def restore():
        if vars(cls).get(method) is patched:
            if owned_attribute:
                setattr(cls, method, original)
            else:
                delattr(cls, method)

    undo.append(restore)
