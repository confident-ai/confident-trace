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


def instrument(rt):
    import crewai  # noqa: F401 — selected adapters alone import their framework.

    previous = getattr(rt, "_crewai_state", None)
    if previous is not None and not previous.closed:
        return []
    state = State(rt)
    rt._crewai_state = state
    undo = []
    for module, class_name, method, kind in TARGETS:
        safe(patch, module, class_name, method, kind, state, undo)
    return [*undo, state.close]


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
