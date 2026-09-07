"""Use agent generators and Tool.__call__ so CodeAgent tools are covered too."""

from functools import partial

from ..._attributes import Integration
from ..._core.safety import safe
from ..._semconv import genai_v1_37_0 as ai
from .._shared.execution import State, execution_wrapper, patch


def describe(kind, instance, params):
    name = getattr(instance, "name", None)
    name = name[:256] if type(name) is str else type(instance).__name__
    attrs = {}
    if kind in ("agent", "tool"):
        operation = "invoke_agent" if kind == "agent" else "execute_tool"
        attrs[ai.GEN_AI_OPERATION_NAME] = operation
        attrs[ai.GEN_AI_AGENT_NAME if kind == "agent" else ai.GEN_AI_TOOL_NAME] = name
    return f"{attrs.get(ai.GEN_AI_OPERATION_NAME, kind)} {name}", attrs


def request(kind, op, instance, params):
    value = (
        params.get("task")
        if kind == "agent"
        else {"args": params.get("args", ()), "kwargs": params.get("kwargs", {})}
        if kind == "tool"
        else None
    )
    if value is not None:
        op.input(value)


def response(op, value):
    from smolagents.agent_types import AgentText
    from smolagents.memory import FinalAnswerStep

    if isinstance(value, AgentText):
        value = str(value)
    if isinstance(value, FinalAnswerStep):
        output = value.output
        op.output(str(output) if isinstance(output, AgentText) else output)
    elif type(value) in (str, int, float, bool, dict, list, tuple) or value is None:
        op.output(value)


def instrument(rt):
    from smolagents.agents import CodeAgent, MultiStepAgent, ToolCallingAgent
    from smolagents.tools import Tool

    previous = getattr(rt, "_smolagents_state", None)
    if previous is not None and not previous.closed:
        return []
    state = State(rt, integration=Integration.SMOLAGENTS)
    rt._smolagents_state = state
    undo = []
    for target, method, kind in (
        (MultiStepAgent, "_run_stream", "agent"),
        (MultiStepAgent, "_generate_planning_step", "planning"),
        (ToolCallingAgent, "_step_stream", "step"),
        (CodeAgent, "_step_stream", "step"),
        (Tool, "__call__", "tool"),
    ):
        safe(
            patch,
            target,
            method,
            execution_wrapper(
                state, partial(describe, kind), partial(request, kind), response
            ),
            undo,
        )
    return [*undo, state.close]
