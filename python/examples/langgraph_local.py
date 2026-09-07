"""Offline graph tracing: install langgraph, then run this file."""

from langgraph.graph import END, START, StateGraph
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import confident_trace as ct


def greet(state):
    with ct.span("format-greeting"):
        return {"greeting": f"Hello, {state['name']}"}


def main():
    exporter = InMemorySpanExporter()
    ct.init(
        tracer_provider=TracerProvider(),
        exporter=exporter,
        instrumentations=("langgraph",),
    )
    graph = (
        StateGraph(dict)
        .add_node("greet", greet)
        .add_edge(START, "greet")
        .add_edge("greet", END)
        .compile()
    )
    with ct.span("request"):
        print(graph.invoke({"name": "world"}, {"configurable": {"thread_id": "demo"}}))
    ct.flush()
    for span in exporter.get_finished_spans():
        print(
            span.name,
            f"trace={span.context.trace_id:032x}",
            f"parent={span.parent.span_id:016x}" if span.parent else "root",
        )
    ct.shutdown()


if __name__ == "__main__":
    main()
