"""Agent/team/workflow and function-call boundaries; no model wrappers."""

import importlib
from functools import partial

from ..._attributes import Integration
from ..._core.safety import safe
from ..._semconv import genai_v1_37_0 as ai
from .._shared.execution import State, execution_wrapper, patch, stream


def describe(kind, instance, params):
    # Background jobs outlive the dispatch method; instrumentation belongs in
    # the worker. Do not misrepresent enqueueing as a completed agent execution.
    if params.get("background"):
        return None
    name = getattr(instance.function if kind == "tool" else instance, "name", None)
    name = name[:256] if type(name) is str else type(instance).__name__
    attrs = {}
    if kind in ("agent", "team", "tool"):
        operation = "execute_tool" if kind == "tool" else "invoke_agent"
        attrs[ai.GEN_AI_OPERATION_NAME] = operation
        attrs[ai.GEN_AI_TOOL_NAME if kind == "tool" else ai.GEN_AI_AGENT_NAME] = name
    session = params.get("session_id") or getattr(instance, "session_id", None)
    if type(session) is str:
        attrs[ai.GEN_AI_CONVERSATION_ID] = session[:4096]
    if kind == "tool" and type(getattr(instance, "call_id", None)) is str:
        attrs[ai.GEN_AI_TOOL_CALL_ID] = instance.call_id
    return f"{attrs.get(ai.GEN_AI_OPERATION_NAME, kind)} {name}", attrs


def request(kind, op, instance, params):
    if kind == "tool":
        value = instance.arguments
    elif kind == "step":
        value = getattr(params.get("step_input"), "input", None)
    else:
        value = params.get("input")
    op.input(value)


def response(op, value):
    # Explicit status/event labels; error strings and session internals stay out.
    event = getattr(value, "event", None)
    status = getattr(value, "status", None)
    if status == "failure" or event in ("RunError", "TeamRunError", "WorkflowError"):
        op.failure("ExecutionFailed")
    if event in ("RunCancelled", "TeamRunCancelled", "WorkflowCancelled"):
        op.failure("CancelledError")
    text = value if type(value) is str else getattr(value, "content", None)
    if text is None:
        text = getattr(value, "result", None)
    if type(text) is str:
        op.output_text(
            text,
            delta=op.streaming
            and (type(value) is str or event in ("RunContent", "TeamRunContent")),
        )
    elif text is not None:
        op.output(text)


def finish_tool(op, value, instance):
    result = getattr(value, "result", None)
    if hasattr(result, "__next__") or hasattr(result, "__anext__"):
        wrapped = stream(op, result, response)
        # Both fields reference this invocation's same iterator in Agno.
        value.result = wrapped
        instance.result = wrapped
        return True
    return False


def instrument(rt):
    from agno.agent import Agent
    from agno.team import Team
    from agno.tools.function import FunctionCall
    from agno.workflow import Workflow
    from agno.workflow.step import Step

    previous = getattr(rt, "_agno_state", None)
    if previous is not None and not previous.closed:
        return []
    state = State(rt, integration=Integration.AGNO)
    rt._agno_state = state
    undo = []
    targets = [
        (Agent, "agent", ("run", "arun")),
        (Team, "team", ("run", "arun")),
        (Workflow, "workflow", ("run", "arun")),
        (Step, "step", ("execute", "aexecute", "execute_stream", "aexecute_stream")),
        (FunctionCall, "tool", ("execute", "aexecute")),
    ]
    # Parallel/conditional containers are workflow structure, not GenAI operations.
    for module, cls in (
        ("parallel", "Parallel"),
        ("condition", "Condition"),
        ("loop", "Loop"),
        ("router", "Router"),
    ):
        target = safe(
            lambda: getattr(importlib.import_module(f"agno.workflow.{module}"), cls)
        )
        if target:
            targets.append(
                (
                    target,
                    "workflow",
                    ("execute", "aexecute", "execute_stream", "aexecute_stream"),
                )
            )
    for target, kind, methods in targets:
        wrapper = execution_wrapper(
            state,
            partial(describe, kind),
            partial(request, kind),
            response,
            finish=finish_tool if kind == "tool" else None,
        )
        for method in methods:
            safe(patch, target, method, wrapper, undo)
    return [*undo, state.close]
