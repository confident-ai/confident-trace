"""Install langgraph and langchain-openai; configure OpenAI and Confident keys."""

import os

from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

import confident_trace as ct


@tool
def lookup(value: str) -> str:
    """Look up the uppercase form of a value."""
    with ct.span("lookup-storage"):
        return value.upper()


def main():
    ct.init()
    try:
        model = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini")).bind_tools(
            [lookup]
        )

        def assistant(state: MessagesState):
            return {"messages": [model.invoke(state["messages"])]}

        graph = (
            StateGraph(MessagesState)
            .add_node("assistant", assistant)
            .add_node("tools", ToolNode([lookup]))
            .add_edge(START, "assistant")
            .add_conditional_edges("assistant", tools_condition)
            .add_edge("tools", "assistant")
            .compile(checkpointer=InMemorySaver())
        )
        config = {"configurable": {"thread_id": "example-conversation"}}
        # Two request traces share conversation metadata and graph memory.
        for prompt in ("Use lookup on hello.", "What value did you just look up?"):
            with ct.span("request", thread_id="example-conversation"):
                result = graph.invoke(
                    {"messages": [{"role": "user", "content": prompt}]}, config
                )
                print(result["messages"][-1].content)
    finally:
        ct.shutdown()


if __name__ == "__main__":
    main()
